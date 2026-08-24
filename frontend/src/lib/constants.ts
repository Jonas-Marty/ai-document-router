// SPEC 5: GET /queue?limit=20. One constant so the query key, the fetch, and any cache
// write after a mutation can never drift out of sync with each other.
export const QUEUE_LIMIT = 20;

// SPEC 8.6: "Load more" via cursor. One page at this size per fetch.
export const HISTORY_PAGE_LIMIT = 20;
