/**
 *
 *
 * @copyright 2017 Government of Canada
 * @license MIT
 * @author igboyes
 *
 */
import { FIND_HMMS, GET_HMM, IMPORT_HMMS } from "../actionTypes";

export const findHMMs = (url) => {
    return {
        type: FIND_HMMS.REQUESTED,
        url
    };
};

export const getHmm = (hmmId) => {
    return {
        type: GET_HMM.REQUESTED,
        hmmId: hmmId
    };
};

export const importHMMs = () => {
    return {
        type: IMPORT_HMMS.REQUESTED
    }
};
