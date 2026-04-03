#!/usr/bin/env node
import { createHash, createPrivateKey, sign } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const args = parseArgs(process.argv.slice(2));

const registryPath = resolve(args.registryFile);
const manifestPath = resolve(args.manifestFile);
const privateKeyPemRaw = process.env[args.privateKeyEnv];

if (!privateKeyPemRaw) {
  throw new Error(
    `Missing ${args.privateKeyEnv}. Cannot sign contracts registry manifest without a private key.`,
  );
}

const privateKeyPem = privateKeyPemRaw.replace(/\\n/g, "\n");
const privateKey = createPrivateKey(privateKeyPem);

const registryText = await readFile(registryPath, "utf8");
const registry = JSON.parse(registryText);

const registryHash = sha256Prefixed(registryText);
const sourceRevision =
  args.sourceRevision ?? deriveSourceRevision(registry) ?? `generatedAt:${new Date().toISOString()}`;

const payload = {
  schema_version: 1,
  registry_hash: registryHash,
  generated_at: new Date().toISOString(),
  source_revision: sourceRevision,
  signature_algorithm: "ed25519",
  ...(args.registryUrl ? { registry_url: args.registryUrl } : {}),
  ...(args.keyId ? { key_id: args.keyId } : {}),
};

const payloadText = serializeManifestPayload(payload);
const signature = sign(null, Buffer.from(payloadText, "utf8"), privateKey).toString("base64");

const manifest = {
  ...payload,
  signature,
};

await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
console.log(`Signed contracts registry manifest: ${manifestPath}`);

function parseArgs(argv) {
  let registryFile = "dist/v1/general/data/contracts_registry.json";
  let manifestFile = "dist/v1/general/data/contracts_registry.manifest.json";
  let sourceRevision;
  let registryUrl;
  let keyId;
  let privateKeyEnv = "CONTRACTS_REGISTRY_SIGNING_PRIVATE_KEY";

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--registry-file") {
      registryFile = argv[++i] ?? registryFile;
      continue;
    }
    if (arg === "--manifest-file") {
      manifestFile = argv[++i] ?? manifestFile;
      continue;
    }
    if (arg === "--source-revision") {
      sourceRevision = argv[++i] ?? sourceRevision;
      continue;
    }
    if (arg === "--registry-url") {
      registryUrl = argv[++i] ?? registryUrl;
      continue;
    }
    if (arg === "--key-id") {
      keyId = argv[++i] ?? keyId;
      continue;
    }
    if (arg === "--private-key-env") {
      privateKeyEnv = argv[++i] ?? privateKeyEnv;
      continue;
    }
    throw new Error(`Unknown argument: ${arg}`);
  }

  return {
    registryFile,
    manifestFile,
    sourceRevision,
    registryUrl,
    keyId,
    privateKeyEnv,
  };
}

function deriveSourceRevision(registry) {
  const sources = registry?.metadata?.sources;
  if (!Array.isArray(sources) || sources.length === 0) {
    return undefined;
  }

  const parts = sources.map((source) => {
    if (!source || typeof source !== "object") return "unknown";
    const name = typeof source.name === "string" ? source.name : "unknown";
    const revision = typeof source.revision === "string" ? source.revision : undefined;
    const hash = typeof source.hash === "string" ? source.hash : undefined;

    if (revision && hash) return `${name}@${revision}#${hash}`;
    if (revision) return `${name}@${revision}`;
    if (hash) return `${name}#${hash}`;
    return name;
  });

  return parts.join("|");
}

function sha256Prefixed(content) {
  const hash = createHash("sha256").update(content, "utf8").digest("hex");
  return `sha256:${hash}`;
}

function serializeManifestPayload(payload) {
  const body = {
    schema_version: payload.schema_version,
    registry_hash: payload.registry_hash,
    generated_at: payload.generated_at,
    source_revision: payload.source_revision,
    signature_algorithm: payload.signature_algorithm,
    ...(payload.registry_url ? { registry_url: payload.registry_url } : {}),
    ...(payload.key_id ? { key_id: payload.key_id } : {}),
  };
  return JSON.stringify(body);
}
