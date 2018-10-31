"""
Job class and functions for NuVs.

"""
import collections
import json
import os
import shlex
import shutil
import tempfile

import pymongo
import pymongo.errors

import virtool.bio
import virtool.db.sync
import virtool.jobs.job


class SubprocessError(Exception):
    pass


class Job(virtool.jobs.job.Job):
    """
    A job class for NuVs, a custom workflow used for identifying potential viral sequences from sample libraries. The
    workflow consists of the following steps:

    1. Eliminate known viral reads by mapping the sample reads to the Virtool otu reference using ``bowtie2`` saving
       unaligned reads.
    2. Eliminate known host reads by mapping the reads remaining from the previous stage to the sample's subtraction
       host using ``bowtie2`` and saving the unaligned reads.
    3. Generate an assembly from the remaining reads using SPAdes.
    4. Extract all significant open reading frames (ORF) from the assembled contigs.
    5. Using HMMER/vFAM, identify possible viral domains in the ORFs.

    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._stage_list = [
            self.make_analysis_dir,
            self.map_otus,
            self.map_subtraction,
            self.reunite_pairs,
            self.assemble,
            self.process_fasta,
            self.press_hmm,
            self.vfam,
            self.import_results
        ]

    def check_db(self):
        """
        Get some initial information from the database that will be required during the course of the job.

        """
        self.params = {
            # The id of the parent sample for the analysis being run.
            "sample_id": self.task_args["sample_id"],

            # The document id for the analysis being run.
            "analysis_id": self.task_args["analysis_id"],

            "ref_id": self.task_args["ref_id"],

            # The path to the directory where all analysis result files will be written.
            "analysis_path": None
        }

        # The document for the sample being analyzed. Assigned after database connection is made.
        sample = self.db.samples.find_one(self.params["sample_id"])

        sample_path = os.path.join(self.settings["data_path"], "samples", self.params["sample_id"])

        self.params.update({
            # The path to the directory where all analysis result files will be written.
            "analysis_path": os.path.join(sample_path, "analysis", self.params["analysis_id"]),

            # The path to a virus Bowtie2 index to subtract against.
            "index_path": os.path.join(
                self.settings["data_path"],
                "references",
                self.params["ref_id"],
                self.task_args["index_id"],
                "reference"
            ),

            # The path to a host Bowtie2 index to subtract against.
            "subtraction_path": os.path.join(
                self.settings["data_path"],
                "subtractions",
                sample["subtraction"]["id"].lower().replace(" ", "_"),
                "reference"
            ),

            "srna": sample.get("srna", False),

            # The number of reads in the sample library. Assigned after database connection is made.
            "read_count": int(sample["quality"]["count"])
        })

        read_paths = [os.path.join(sample_path, "reads_1.fastq")]

        paired = sample.get("paired", None)

        if paired is None:
            paired = len(sample["files"]) == 2

        if paired:
            read_paths.append(os.path.join(sample_path, "reads_2.fastq"))

        self.params.update({
            "read_paths": read_paths,
            "paired": paired
        })

    def make_analysis_dir(self):
        """
        Make a directory for the analysis in the sample/analysis directory.

        """
        os.mkdir(self.params["analysis_path"])

    def map_otus(self):
        """
        Maps reads to the main otu reference using ``bowtie2``. Bowtie2 is set to use the search parameter
        ``--very-fast-local`` and retain unaligned reads to the FASTA file ``unmapped_otus.fq``.

        """
        command = [
            "bowtie2",
            "-p", str(self.proc),
            "-k", str(1),
            "--very-fast-local",
            "-x", self.params["index_path"],
            "--un", os.path.join(self.params["analysis_path"], "unmapped_otus.fq"),
            "-U", ",".join(self.params["read_paths"])
        ]

        self.run_subprocess(command)

    def map_subtraction(self):
        """
        Maps unaligned reads from :meth:`.map_otus` to the sample's subtraction host using ``bowtie2``. Bowtie2 is
        set to use the search parameter ``--very-fast-local`` and retain unaligned reads to the FASTA file
        ``unmapped_host.fq``.

        """
        command = [
            "bowtie2",
            "--very-fast-local",
            "-k", str(1),
            "-p", str(self.proc),
            "-x", shlex.quote(self.params["subtraction_path"]),
            "--un", os.path.join(self.params["analysis_path"], "unmapped_hosts.fq"),
            "-U", os.path.join(self.params["analysis_path"], "unmapped_otus.fq"),
        ]

        self.run_subprocess(command)

    def reunite_pairs(self):
        if self.params["paired"]:
            unmapped_path = os.path.join(self.params["analysis_path"], "unmapped_hosts.fq")
            headers = virtool.bio.read_fastq_headers(unmapped_path)

            unmapped_roots = {h.split(" ")[0] for h in headers}

            with open(os.path.join(self.params["analysis_path"], "unmapped_1.fq"), "w") as f:
                for header, seq, quality in virtool.bio.read_fastq(self.params["read_paths"][0]):
                    if header.split(" ")[0] in unmapped_roots:
                        f.write("\n".join([header, seq, "+", quality]) + "\n")

            with open(os.path.join(self.params["analysis_path"], "unmapped_2.fq"), "w") as f:
                for header, seq, quality in virtool.bio.read_fastq(self.params["read_paths"][1]):
                    if header.split(" ")[0] in unmapped_roots:
                        f.write("\n".join([header, seq, "+", quality]) + "\n")

    def assemble(self):
        """
        Call ``spades.py`` to assemble contigs from ``unmapped_hosts.fq``. Passes ``21,33,55,75`` for the ``-k``
        argument.

        """
        command = [
            "spades.py",
            "-t", str(self.proc - 1),
            "-m", str(self.mem)
        ]

        if self.params["paired"]:
            command += [
                "-1", os.path.join(self.params["analysis_path"], "unmapped_1.fq"),
                "-2", os.path.join(self.params["analysis_path"], "unmapped_2.fq"),
            ]
        else:
            command += [
                "-s", os.path.join(self.params["analysis_path"], "unmapped_hosts.fq"),
            ]

        temp_dir = tempfile.TemporaryDirectory()

        temp_path = temp_dir.name

        k = "21,33,55,75"

        if self.params["srna"]:
            k = "17,21,23"

        command += [
            "-o", temp_path,
            "-k", k
        ]

        def stdout_handler(line):
            self.add_log(line.rstrip(), indent=4)

        try:
            self.run_subprocess(command, stdout_handler=stdout_handler)
        except SubprocessError:
            spades_log_path = os.path.join(temp_path, "spades.log")

            if os.path.isfile(spades_log_path):
                with open(spades_log_path, "r") as f:
                    if "Error in malloc(): out of memory" in f.read():
                        raise SubprocessError("Out of memory")

            raise

        shutil.copyfile(
            os.path.join(temp_path, "scaffolds.fasta"),
            os.path.join(self.params["analysis_path"], "assembly.fa")
        )

        temp_dir.cleanup()

    def process_fasta(self):
        """
        Finds ORFs in the contigs assembled by :meth:`.assemble`. Only ORFs that are 100+ amino acids long are recorded.
        Contigs with no acceptable ORFs are discarded.

        """
        # Contigs that contain at least one acceptable ORF.
        self.results = list()

        assembly_path = os.path.join(self.params["analysis_path"], "assembly.fa")

        assembly = virtool.bio.read_fasta(assembly_path)

        for _, sequence in assembly:

            sequence_length = len(sequence)

            # Don't consider the sequence if it is shorter than 300 bp.
            if sequence_length < 300:
                continue

            orfs = virtool.bio.find_orfs(sequence)

            # Don't consider the sequence if it has no ORFs.
            if len(orfs) == 0:
                continue

            # Add an index field to each orf dict.
            orfs = [dict(o, index=i) for i, o in enumerate(orfs)]

            for orf in orfs:
                orf.pop("nuc")
                orf["hits"] = list()

            # Make an entry for the nucleotide sequence containing a unique integer index, the sequence itself, and
            # all ORFs in the sequence.
            self.results.append({
                "index": len(self.results),
                "sequence": sequence,
                "orfs": orfs
            })

        # Write the ORFs to a FASTA file so that they can be analyzed using HMMER and vFAM.
        with open(os.path.join(self.params["analysis_path"], "orfs.fa"), "w") as f:
            for entry in self.results:
                for orf in entry["orfs"]:
                    f.write(">sequence_{}.{}\n{}\n".format(entry["index"], orf["index"], orf["pro"]))

    def press_hmm(self):

        shutil.copy(os.path.join(self.settings["data_path"], "hmm", "profiles.hmm"), self.params["analysis_path"])

        hmm_path = os.path.join(self.params["analysis_path"], "profiles.hmm")

        command = [
            "hmmpress",
            hmm_path
        ]

        self.run_subprocess(command)

        os.remove(hmm_path)

    def vfam(self):
        """
        Searches for viral motifs in ORF translations generated by :meth:`.process_fasta`. Calls ``hmmscan`` and
        searches against ``candidates.fa`` using the profile HMMs in ``data_path/hmm/vFam.hmm``.

        Saves two files:

        - ``hmm.tsv`` contains the raw output of `hmmer`
        - ``hits.tsv`` contains the `hmmer` results formatted and annotated with the annotations from the Virtool HMM
          database collection

        """
        # The path to output the hmmer results to.
        tsv_path = os.path.join(self.params["analysis_path"], "hmm.tsv")

        command = [
            "hmmscan",
            "--tblout", tsv_path,
            "--noali",
            "--cpu", str(self.proc - 1),
            os.path.join(self.params["analysis_path"], "profiles.hmm"),
            os.path.join(self.params["analysis_path"], "orfs.fa")
        ]

        self.run_subprocess(command)

        hits = collections.defaultdict(lambda: collections.defaultdict(list))

        # Go through the raw HMMER results and annotate the HMM hits with data from the database.
        with open(tsv_path, "r") as hmm_file:
            for line in hmm_file:
                if line.startswith("vFam"):
                    line = line.split()

                    cluster_id = int(line[0].split("_")[1])
                    annotation_id = (self.db.hmm.find_one({"cluster": int(cluster_id)}, {"_id": True}))["_id"]

                    # Expecting sequence_0.0
                    sequence_index, orf_index = (int(x) for x in line[2].split("_")[1].split("."))

                    hits[sequence_index][orf_index].append({
                        "hit": annotation_id,
                        "full_e": float(line[4]),
                        "full_score": float(line[5]),
                        "full_bias": float(line[6]),
                        "best_e": float(line[7]),
                        "best_bias": float(line[8]),
                        "best_score": float(line[9])
                    })

        for sequence_index in hits:
            for orf_index in hits[sequence_index]:
                self.results[sequence_index]["orfs"][orf_index]["hits"] = hits[sequence_index][orf_index]

            sequence = self.results[sequence_index]

            if all(len(o["hits"]) == 0 for o in sequence["orfs"]):
                self.results.remove(sequence)

    def import_results(self):
        """
        Save the results to the analysis document and set the ``ready`` field to ``True``.

        After the import is complete, :meth:`.indexes.Collection.cleanup_index_files` is called to remove any otus
        indexes that are no longer being used by an active analysis job.

        """
        analysis_id = self.params["analysis_id"]
        sample_id = self.params["sample_id"]

        try:
            self.db.analyses.update_one({"_id": analysis_id}, {
                "$set": {
                    "results": self.results,
                    "ready": True
                }
            })
        except pymongo.errors.DocumentTooLarge:
            with open(os.path.join(self.params["analysis_path"], "nuvs.json"), "w") as f:
                json_string = json.dumps(self.results)
                f.write(json_string)

            self.db.analyses.update_one({"_id": analysis_id}, {
                "$set": {
                    "results": "file",
                    "ready": True
                }
            })

        virtool.db.sync.recalculate_algorithm_tags(self.db, sample_id)

        self.dispatch("analyses", "update", [analysis_id])
        self.dispatch("samples", "update", [sample_id])

    def cleanup(self):
        try:
            self.temp_dir.cleanup()
        except AttributeError:
            pass

        virtool.db.sync.recalculate_algorithm_tags(self.db, self.params["sample_id"])

        super().cleanup()
