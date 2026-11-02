// SPEC 5: GET /queue?limit=20. One constant so the query key, the fetch, and any cache
// write after a mutation can never drift out of sync with each other.
export const QUEUE_LIMIT = 20;

// SPEC 8.6: "Load more" via cursor. One page at this size per fetch.
export const HISTORY_PAGE_LIMIT = 20;

// SPEC 8.11: the Activity page's page size, and the window its usage summary covers.
export const AUDIT_PAGE_LIMIT = 50;
export const AUDIT_USAGE_DAYS = 30;
