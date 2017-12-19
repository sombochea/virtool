import { put, select, takeEvery, takeLatest, throttle } from "redux-saga/effects";

import jobsAPI from "./api";
import { setPending } from "../sagaHelpers";
import { WS_UPDATE_JOB, FIND_JOBS, GET_JOB, CANCEL_JOB, REMOVE_JOB, CLEAR_JOBS, GET_RESOURCES } from "../actionTypes";

export function* wsUpdateJob (action) {
    yield findJobs(action);
    const detail = yield select(state => state.jobs.detail);

    if (detail !== null && detail.id === action.data.id) {
        yield getJob({id: detail.id});
    }
}

export function* findJobs (action) {
    try {
        const response = yield jobsAPI.find(action.term, action.page);
        yield put({type: FIND_JOBS.SUCCEEDED, data: response.body});
    } catch (error) {
        yield put({type: FIND_JOBS.FAILED, error});
    }
}

export function* findJobsWithPending (action) {
    yield setPending(findJobs, action);
}

export function* getJobWithPending (action) {
    yield setPending(getJob, action);
}

export function* getJob (action) {
    try {
        const response = yield jobsAPI.get(action.jobId);
        yield put({type: GET_JOB.SUCCEEDED, data: response.body});
    } catch (error) {
        yield put({type: GET_JOB.FAILED, error});
    }
}

export function* cancelJob (action) {
    yield setPending(function* () {
        try {
            const response = yield jobsAPI.cancel(action.jobId);
            yield put({type: CANCEL_JOB.SUCCEEDED, data: response.body});
        } catch (error) {
            yield put({type: CANCEL_JOB.FAILED, error});
        }
    }, action);
}

export function* removeJob (action) {
    yield setPending(function* () {
        try {
            yield jobsAPI.remove(action.jobId);
            yield put({type: REMOVE_JOB.SUCCEEDED, jobId: action.jobId});
        } catch (error) {
            yield put({type: REMOVE_JOB.FAILED}, error);
        }
    }, action);
}

export function* clearJobs (action) {
    yield setPending(function* (action) {
        try {
            yield jobsAPI.clear(action.scope);
            yield put({type: REMOVE_JOB.SUCCEEDED});
            yield put({type: FIND_JOBS.REQUESTED});
        } catch (error) {
            yield put({type: REMOVE_JOB.FAILED}, error);
        }
    }, action);
}

export function* getResources () {
    try {
        const response = yield jobsAPI.getResources();
        yield put({type: GET_RESOURCES.SUCCEEDED, data: response.body});
    } catch (error) {
        yield put({type: GET_RESOURCES.FAILED}, error);
    }
}

export function* watchJobs () {
    yield takeLatest(WS_UPDATE_JOB, wsUpdateJob);
    yield throttle(250, FIND_JOBS.REQUESTED, findJobsWithPending);
    yield takeLatest(GET_JOB.REQUESTED, getJobWithPending);
    yield takeEvery(CANCEL_JOB.REQUESTED, cancelJob);
    yield takeEvery(REMOVE_JOB.REQUESTED, removeJob);
    yield takeLatest(CLEAR_JOBS.REQUESTED, clearJobs);
    yield takeLatest(GET_RESOURCES.REQUESTED, getResources);
}
