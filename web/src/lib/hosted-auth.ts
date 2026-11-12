import { createHash } from "node:crypto";

export function hostedHeaders(incoming: Headers): Record<string, string> {
  if (process.env.HOSTED_MODE !== "true") return {};
  const token = process.env.PROXY_AUTH_TOKEN;
  if (!token || token.length < 32) throw new Error("Hosted proxy token is missing or too short.");
  // ACA authentication must validate and replace these headers before ingress.
  const principal = incoming.get("x-ms-client-principal-id");
  if (!principal) throw new Error("Entra authentication is required.");
  const hash = (value: string) => createHash("sha256").update(value).digest("hex");
  const ip = incoming.get("x-forwarded-for")?.split(",").at(-1)?.trim() || principal;
  return {
    "X-Gridcast-Proxy-Token": token,
    "X-Gridcast-User": hash(principal),
    "X-Gridcast-Client-IP": hash(ip),
  };
}
