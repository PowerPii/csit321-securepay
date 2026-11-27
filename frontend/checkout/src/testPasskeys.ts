// Test helpers: a fake WebAuthn device for jsdom (passkeys.test.ts, App.test.tsx).
import { vi } from "vitest";

const bytes = (...n: number[]) => new Uint8Array(n).buffer;

export function stubPasskeyDevice(answer: { get?: () => unknown; create?: () => unknown }) {
  vi.stubGlobal("PublicKeyCredential", function PublicKeyCredential() {});
  const credentials = { get: vi.fn(async () => answer.get?.()), create: vi.fn(async () => answer.create?.()) };
  Object.defineProperty(navigator, "credentials", { value: credentials, configurable: true });
  return credentials;
}

export const assertion = () => ({
  id: "AQID", rawId: bytes(1, 2, 3), type: "public-key", authenticatorAttachment: "platform",
  getClientExtensionResults: () => ({}),
  response: { clientDataJSON: bytes(123, 125), authenticatorData: bytes(9), signature: bytes(8, 7), userHandle: null },
});

