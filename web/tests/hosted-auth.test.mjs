import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { afterEach, test } from "node:test";
import { hostedHeaders } from "../src/lib/hosted-auth.ts";

const originalMode = process.env.HOSTED_MODE;
const originalToken = process.env.PROXY_AUTH_TOKEN;
afterEach(() => {
  for (const [key, value] of [["HOSTED_MODE", originalMode], ["PROXY_AUTH_TOKEN", originalToken]]) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
});

test("local mode does not forward caller-supplied identity or credentials", () => {
  delete process.env.HOSTED_MODE;
  assert.deepEqual(hostedHeaders(new Headers({ "X-Gridcast-Proxy-Token": "spoof" })), {});
});

test("hosted mode fails closed without a configured token or Entra identity", () => {
  process.env.HOSTED_MODE = "true";
  delete process.env.PROXY_AUTH_TOKEN;
  assert.throws(() => hostedHeaders(new Headers()), /token/);
  process.env.PROXY_AUTH_TOKEN = "t".repeat(32);
  assert.throws(() => hostedHeaders(new Headers()), /authentication/);
});

test("only platform principal and last proxy hop form pseudonymous admission identities", () => {
  process.env.HOSTED_MODE = "true";
  process.env.PROXY_AUTH_TOKEN = "t".repeat(32);
  const hash = (value) => createHash("sha256").update(value).digest("hex");
  assert.deepEqual(hostedHeaders(new Headers({
    "x-ms-client-principal-id": "platform-user",
    "x-forwarded-for": "caller-spoof, 192.0.2.1",
    "X-Gridcast-User": "caller-spoof",
    "X-Gridcast-Proxy-Token": "caller-spoof",
  })), {
    "X-Gridcast-Proxy-Token": "t".repeat(32),
    "X-Gridcast-User": hash("platform-user"),
    "X-Gridcast-Client-IP": hash("192.0.2.1"),
  });
});
