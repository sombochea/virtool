import shutil
from pathlib import Path
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

import virtool.pg.utils
import virtool.utils
from virtool.caches.models import SampleArtifactCache, SampleReadsCache
from virtool.samples.models import SampleArtifact, SampleReads
from virtool.types import App
from virtool.uploads.models import Upload


async def get_existing_reads(
    pg: AsyncEngine, sample: str, cache: bool = False
) -> List[str]:
    """
    Get reads files in either `sample_reads_files` or `sample_reads_files_cache` depending on value of `cache`.

    :param pg: PostgreSQL AsyncEngine object
    :param sample: ID that corresponds to a parent sample
    :param cache: Whether it should check the `sample_reads_files` or `sample_reads_files_cache` SQL table
    :return: List of reads file names that are in a given table
    """
    async with AsyncSession(pg) as session:
        query = await session.execute(
            select(SampleReads if not cache else SampleReadsCache).filter_by(
                sample=sample
            )
        )

    return [result.name for result in query.scalars().all()]


async def create_artifact_file(
    pg: AsyncEngine, name: str, sample: str, artifact_type: str, cache: bool = False
) -> Dict[str, any]:
    """
    Create a row in an SQL table that represents uploaded sample artifact files. A row is created in either the
    `sample_artifact` or `sample_artifact_cache` table depending on the value of `cache`.

    :param pg: PostgreSQL AsyncEngine object
    :param name: Name of the sample artifact file
    :param sample: ID that corresponds to a parent sample
    :param artifact_type: Type of artifact to be uploaded
    :param cache: Whether the row should be created in the `sample_artifact` or `sample_artifact_cache` table
    :return: A dictionary representation of the newly created row
    """
    async with AsyncSession(pg) as session:
        artifact = SampleArtifact() if not cache else SampleArtifactCache()

        artifact.sample, artifact.name, artifact.type = sample, name, artifact_type

        session.add(artifact)
        await session.flush()

        artifact.name_on_disk = f"{artifact.id}-{artifact.name}"

        artifact = artifact.to_dict()

        await session.commit()

        return artifact


async def create_reads_file(
    app: App,
    size: int,
    name: str,
    name_on_disk: str,
    sample_id: str,
    cache: bool = False,
    upload_id: int = None,
    path: Path = None,
    copy_file: bool = False
) -> Dict[str, any]:
    """
    Create a row in a SQL table that represents uploaded sample reads files.

    :param app: The application object
    :param size: Size of a newly uploaded file in bytes
    :param name: Name of the file (either `reads_1.fq.gz` or `reads_2.fq.gz`)
    :param name_on_disk: Name of the newly uploaded file on disk
    :param sample_id: ID that corresponds to a parent sample
    :param cache: Whether the row should be created in the `sample_reads_files` or `sample_reads_files_cache` table
    :param upload_id: ID for a row in the `uploads` table to pair with
    :param path: The path to the reads file
    :param copy_file: Whether the file should be copied to the data path.

    :return: List of dictionary representations of the newly created row(s)
    """

    async with AsyncSession(app["pg"]) as session:
        reads = SampleReads() if not cache else SampleReadsCache()

        reads.sample, reads.name, reads.name_on_disk, reads.size, reads.uploaded_at = (
            sample_id,
            name,
            name_on_disk,
            size,
            virtool.utils.timestamp(),
        )

        if upload_id and (upload := (await session.execute(select(Upload).filter_by(id=upload_id))).scalar()):
            upload.reads.append(reads)

        session.add(reads)

        await session.flush()

        reads = reads.to_dict()

        await session.commit()

    if path and copy_file:
        reads_path = app["settings"]["data_path"] / "samples" / sample_id
        reads_path.mkdir(parents=True, exist_ok=True)

        await app["run_in_thread"](shutil.copy, path, reads_path / name)

    return reads
