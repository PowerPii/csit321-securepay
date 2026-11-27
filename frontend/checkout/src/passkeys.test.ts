import { afterEach, describe, expect, it, vi } from "vitest";
import { assertion, stubPasskeyDevice } from "./testPasskeys";
import { createPasskey, fromB64url, PasskeyCancelled, passkeysSupported, signWithPasskey, toB64url } from "./passkeys";

const bytes = (...n: number[]) => new Uint8Array(n).buffer;

afterEach(() => {
  vi.unstubAllGlobals();
  Object.defineProperty(navigator, "credentials", { value: undefined, configurable: true });
});

describe("passkeys", () => {
  it("round-trips base64url without padding", () => {
    for (const data of [bytes(), bytes(255), bytes(251, 255, 191), bytes(0, 1, 2, 3, 4)]) {
      const text = toB64url(data)!;
      expect(text).not.toMatch(/[+/=]/);
      expect(new Uint8Array(fromB64url(text))).toEqual(new Uint8Array(data));
    }
    expect(toB64url(bytes(251, 255))).toBe("-_8");
    expect(toB64url(null)).toBeNull();
  });

  it("is not offered where the browser has no WebAuthn", () => {
    expect(passkeysSupported()).toBe(false);
  });

  it("turns the gateway's options into bytes and the answer back into text", async () => {
    const device = stubPasskeyDevice({ get: assertion });
    const answer = await signWithPasskey({ challenge: "AAEC", rpId: "gateway.localhost",
      allowCredentials: [{ id: "AQID", type: "public-key", transports: ["internal"] }], userVerification: "required" });
    const asked = (device.get.mock.calls[0] as unknown as [CredentialRequestOptions])[0].publicKey!;
    expect(new Uint8Array(asked.challenge as ArrayBuffer)).toEqual(new Uint8Array([0, 1, 2]));
    expect(new Uint8Array(asked.allowCredentials![0].id as ArrayBuffer)).toEqual(new Uint8Array([1, 2, 3]));
    expect(answer).toEqual({
      id: "AQID", rawId: "AQID", type: "public-key", authenticatorAttachment: "platform", clientExtensionResults: {},
      response: { clientDataJSON: "e30", authenticatorData: "CQ", signature: "CAc", userHandle: null },
    });
  });

  it("makes a passkey from registration options", async () => {
    const device = stubPasskeyDevice({ create: () => ({
      ...assertion(), response: { clientDataJSON: bytes(1), attestationObject: bytes(2), getTransports: () => ["internal"] },
    }) });
    const answer = await createPasskey({ challenge: "AAEC", rp: { id: "x", name: "SecurePay" },
      user: { id: "Y3Vz", name: "jane@example.com", displayName: "Jane" }, pubKeyCredParams: [], excludeCredentials: [] });
    const asked = (device.create.mock.calls[0] as unknown as [CredentialCreationOptions])[0].publicKey!;
    expect(new TextDecoder().decode(asked.user.id as ArrayBuffer)).toBe("cus");
    expect(answer.response).toEqual({ clientDataJSON: "AQ", attestationObject: "Ag", transports: ["internal"] });
  });

  it("reports a cancelled prompt as PasskeyCancelled", async () => {
    stubPasskeyDevice({ get: () => { throw new DOMException("no", "NotAllowedError"); } });
    await expect(signWithPasskey({ challenge: "AA" })).rejects.toBeInstanceOf(PasskeyCancelled);
  });
});
