/** Thrown by HttpApiClient for every non-2xx response, and for network failures.
 *
 * `code` is one of the SPEC 5 codes from the backend ("not_found", "validation_error", ...)
 * for a real API error, or the client-only sentinel "network_error" when the request never
 * reached the server at all -- e.g. the backend is stopped. Callers that need to tell those
 * apart (the outage banner) check `code === "network_error"`.
 */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number | null;

  constructor(code: string, message: string, status: number | null) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

export const NETWORK_ERROR_CODE = "network_error";
