/** The server sends UTC timestamps without a zone suffix; read them as UTC, not local time. */
export function serverDate(value: string): Date {
  return new Date(/(Z|[+-]\d\d:\d\d)$/.test(value) ? value : `${value}Z`);
}
