// SPEC 5: GET /queue?limit=20. One constant so the query key, the fetch, and any cache
// write after a mutation can never drift out of sync with each other.
export const QUEUE_LIMIT = 20;

// SPEC 8.6: the Inbox grid's page; divisible by every column count it uses (1 to 4).
export const INBOX_PAGE_LIMIT = 48;

// SPEC 8.11: the Activity page's page size, and the window its usage summary covers.
export const AUDIT_PAGE_LIMIT = 50;
export const AUDIT_USAGE_DAYS = 30;
