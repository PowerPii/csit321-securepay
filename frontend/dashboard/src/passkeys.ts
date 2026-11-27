// v2 passkeys (docs/v2/PASSKEYS.md), the same file as frontend/checkout/src/passkeys.ts: turn the gateway's JSON options into what
// navigator.credentials needs, and the device's answer back into JSON. The gateway does
// every check; this file only converts between base64url text and bytes.

type Json = Record<string, unknown>;

export class PasskeyCancelled extends Error {
  constructor() {
    super("The passkey request was cancelled or timed out.");
  }
}

export function passkeysSupported(): boolean {
  return typeof window !== "undefined" && typeof window.PublicKeyCredential === "function"
    && !!navigator.credentials;
}

export function fromB64url(text: string): ArrayBuffer {
  const b64 = text.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((text.length + 3) % 4);
  const raw = atob(b64);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
  return bytes.buffer;
}

export function toB64url(buffer: ArrayBuffer | null | undefined): string | null {
  if (!buffer) return null;
  const bytes = new Uint8Array(buffer);
  let raw = "";
  for (const b of bytes) raw += String.fromCharCode(b);
  return btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

const descriptors = (list: unknown) =>
  Array.isArray(list)
    ? list.map((c: { id: string; type: string; transports?: string[] }) => ({
        ...c, id: fromB64url(c.id), type: "public-key" as const,
        transports: c.transports as AuthenticatorTransport[] | undefined,
      }))
    : undefined;

function creationOptions(o: Json): PublicKeyCredentialCreationOptions {
  const user = o.user as { id: string; name: string; displayName: string };
  return {
    ...(o as unknown as PublicKeyCredentialCreationOptions),
    challenge: fromB64url(o.challenge as string),
    user: { ...user, id: fromB64url(user.id) },
    excludeCredentials: descriptors(o.excludeCredentials),
  };
}

function requestOptions(o: Json): PublicKeyCredentialRequestOptions {
  return {
    ...(o as unknown as PublicKeyCredentialRequestOptions),
    challenge: fromB64url(o.challenge as string),
    allowCredentials: descriptors(o.allowCredentials),
  };
}

async function ask<T>(run: () => Promise<Credential | null>): Promise<T> {
  let credential: Credential | null;
  try {
    credential = await run();
  } catch (err) {
    // NotAllowedError: the person cancelled, or the prompt timed out.
    if (err instanceof DOMException && (err.name === "NotAllowedError" || err.name === "AbortError")) {
      throw new PasskeyCancelled();
    }
    throw err;
  }
  if (!credential) throw new PasskeyCancelled();
  return credential as T;
}

function base(c: PublicKeyCredential) {
  return {
    id: c.id,
    rawId: toB64url(c.rawId),
    type: c.type,
    authenticatorAttachment: c.authenticatorAttachment ?? null,
    clientExtensionResults: c.getClientExtensionResults?.() ?? {},
  };
}

/** Make a new passkey from registration options; returns the answer for the gateway. */
export async function createPasskey(options: Json): Promise<Json> {
  const c = await ask<PublicKeyCredential>(() =>
    navigator.credentials.create({ publicKey: creationOptions(options) }));
  const r = c.response as AuthenticatorAttestationResponse;
  return {
    ...base(c),
    response: {
      clientDataJSON: toB64url(r.clientDataJSON),
      attestationObject: toB64url(r.attestationObject),
      transports: r.getTransports?.() ?? [],
    },
  };
}

/** Sign a challenge with a passkey; returns the answer for the gateway. */
export async function signWithPasskey(options: Json): Promise<Json> {
  const c = await ask<PublicKeyCredential>(() =>
    navigator.credentials.get({ publicKey: requestOptions(options) }));
  const r = c.response as AuthenticatorAssertionResponse;
  return {
    ...base(c),
    response: {
      clientDataJSON: toB64url(r.clientDataJSON),
      authenticatorData: toB64url(r.authenticatorData),
      signature: toB64url(r.signature),
      userHandle: toB64url(r.userHandle),
    },
  };
}
