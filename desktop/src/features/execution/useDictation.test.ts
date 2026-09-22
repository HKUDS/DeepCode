import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import type {
  DictationStatusResult,
  MethodParams,
  MethodResults,
} from "../../generated/app-server";
import type { ClientRuntime, RpcMethod } from "../../rpc/contracts";
import { useDictation } from "./useDictation";

const MODEL = "mlx-community/parakeet-tdt-0.6b-v3";
const PAYLOAD = new Uint8Array([0x1a, 0x45, 0xdf, 0xa3, 0x00, 0x01, 0x02, 0x03]);

class DictationRuntime {
  status: DictationStatusResult = {
    available: true,
    model: MODEL,
    maxAudioSeconds: 120,
  };
  statusError: Error | null = null;
  transcript = { text: "olá mundo", model: MODEL };
  transcribeError: Error | null = null;
  holdTranscribe = false;
  readonly requests: Array<{ method: RpcMethod; params: unknown }> = [];
  private pending: Array<() => void> = [];

  async request<M extends RpcMethod>(
    method: M,
    params: MethodParams[M],
  ): Promise<MethodResults[M]> {
    this.requests.push({ method, params });
    if (method === "dictation/status") {
      if (this.statusError) throw this.statusError;
      return this.status as MethodResults[M];
    }
    if (method !== "dictation/transcribe") {
      throw new Error(`Unexpected method: ${method}`);
    }
    if (this.holdTranscribe) {
      await new Promise<void>((resolve) => this.pending.push(resolve));
    }
    if (this.transcribeError) throw this.transcribeError;
    return this.transcript as MethodResults[M];
  }

  releaseTranscribe() {
    const pending = this.pending;
    this.pending = [];
    for (const resolve of pending) resolve();
  }

  get methods(): string[] {
    return this.requests.map((request) => request.method);
  }

  transcribeParams(): MethodParams["dictation/transcribe"] {
    const request = this.requests.find(
      (entry) => entry.method === "dictation/transcribe",
    );
    expect(request).toBeDefined();
    return request?.params as MethodParams["dictation/transcribe"];
  }
}

class FakeTrack {
  stopped = false;

  stop() {
    this.stopped = true;
  }
}

class FakeStream {
  readonly track = new FakeTrack();

  getTracks(): FakeTrack[] {
    return [this.track];
  }
}

class FakeRecorder {
  static supported: string[] = ["audio/webm", "audio/ogg", "audio/mp4"];
  static created: FakeRecorder[] = [];
  static payload: Uint8Array | null = PAYLOAD;
  /** Container the recorder reports once running, as a real one does. */
  static recordedType: string | null = null;

  static isTypeSupported(type: string): boolean {
    return FakeRecorder.supported.includes(type);
  }

  state: "inactive" | "recording" | "paused" = "inactive";
  mimeType: string;
  ondataavailable: ((event: BlobEvent) => void) | null = null;
  onstop: (() => void) | null = null;

  constructor(
    readonly stream: FakeStream,
    readonly options?: { mimeType?: string; audioBitsPerSecond?: number },
  ) {
    this.mimeType = FakeRecorder.recordedType ?? options?.mimeType ?? "";
    FakeRecorder.created.push(this);
  }

  start() {
    this.state = "recording";
  }

  stop() {
    if (this.state === "inactive") return;
    this.state = "inactive";
    const payload = FakeRecorder.payload;
    if (payload) {
      this.ondataavailable?.({
        data: new Blob([payload.buffer as ArrayBuffer], { type: this.mimeType }),
      } as BlobEvent);
    }
    this.onstop?.();
  }
}

let stream: FakeStream;
let getUserMedia: ReturnType<typeof vi.fn>;

function installRecorderEnvironment() {
  stream = new FakeStream();
  getUserMedia = vi.fn(async () => stream as unknown as MediaStream);
  FakeRecorder.created = [];
  FakeRecorder.supported = ["audio/webm", "audio/ogg", "audio/mp4"];
  FakeRecorder.recordedType = null;
  FakeRecorder.payload = PAYLOAD;
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  Object.defineProperty(navigator, "mediaDevices", {
    value: { getUserMedia },
    configurable: true,
  });
}

function removeRecorderEnvironment() {
  vi.stubGlobal("MediaRecorder", undefined);
  Object.defineProperty(navigator, "mediaDevices", {
    value: undefined,
    configurable: true,
  });
}

/**
 * Let pending promises and React work settle.
 *
 * Fake timers are installed, so `waitFor` (which polls on a real timer) cannot
 * be used here; this drains the microtask queue and any zero-delay timer
 * instead, which is what every awaited step in the hook produces.
 */
async function flush(rounds = 8) {
  await act(async () => {
    for (let round = 0; round < rounds; round += 1) {
      vi.advanceTimersByTime(0);
      await Promise.resolve();
    }
  });
}

function renderDictation(backend: DictationRuntime) {
  const onTranscript = vi.fn();
  const view = renderHook(() =>
    useDictation({
      runtime: backend as unknown as ClientRuntime,
      onTranscript,
    }),
  );
  return { ...view, onTranscript };
}

async function ready(backend: DictationRuntime) {
  const view = renderDictation(backend);
  await flush();
  expect(view.result.current.available).toBe(true);
  return view;
}

beforeEach(() => {
  vi.useFakeTimers();
  installRecorderEnvironment();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("offers no microphone when the server reports dictation unavailable", async () => {
  const backend = new DictationRuntime();
  backend.status = { available: false, model: null, maxAudioSeconds: null };
  const { result } = renderDictation(backend);

  await flush();
  expect(result.current.available).toBe(false);

  await act(async () => result.current.toggle());

  expect(getUserMedia).not.toHaveBeenCalled();
  expect(backend.methods).toEqual(["dictation/status"]);
});

test("treats a failing status request as unavailable", async () => {
  const backend = new DictationRuntime();
  backend.statusError = new Error("method not found: dictation/status");
  const { result } = renderDictation(backend);

  await flush();
  expect(backend.methods).toContain("dictation/status");
  expect(result.current.available).toBe(false);
});

test("records a clip, uploads it, and hands the transcript to the caller", async () => {
  const backend = new DictationRuntime();
  const { result, onTranscript } = await ready(backend);

  await act(async () => result.current.toggle());
  await flush();
  expect(result.current.recording).toBe(true);
  expect(FakeRecorder.created[0].options?.mimeType).toBe("audio/webm");

  await act(async () => {
    vi.advanceTimersByTime(2000);
  });
  expect(result.current.elapsedSeconds).toBe(2);

  await act(async () => result.current.toggle());
  await flush();

  expect(onTranscript).toHaveBeenCalledWith("olá mundo");
  const params = backend.transcribeParams();
  expect(params.mimeType).toBe("audio/webm");
  expect(atob(params.audio)).toBe(
    String.fromCharCode(...PAYLOAD),
  );
  expect(result.current.recording).toBe(false);
  expect(result.current.transcribing).toBe(false);
  expect(result.current.elapsedSeconds).toBe(0);
});

test("reports the browser's container when the preferred one is unsupported", async () => {
  // WKWebView records MP4 only; the upload must follow what was recorded.
  FakeRecorder.supported = [];
  FakeRecorder.recordedType = "audio/mp4;codecs=mp4a.40.2";
  const backend = new DictationRuntime();
  const { result, onTranscript } = await ready(backend);

  await act(async () => result.current.toggle());
  // No preferred container: the recorder picks its own, but the bitrate cap
  // that keeps a full clip under the payload limit is always requested.
  expect(FakeRecorder.created[0].options?.mimeType).toBeUndefined();
  expect(FakeRecorder.created[0].options?.audioBitsPerSecond).toBe(32_000);

  await act(async () => result.current.toggle());
  await flush();

  expect(onTranscript).toHaveBeenCalled();
  expect(backend.transcribeParams().mimeType).toBe("audio/mp4");
});

test("cancelling discards the clip without uploading anything", async () => {
  const backend = new DictationRuntime();
  const { result } = await ready(backend);

  await act(async () => result.current.toggle());
  await act(async () => {
    vi.advanceTimersByTime(3000);
  });
  await act(async () => result.current.cancel());

  expect(backend.methods).toEqual(["dictation/status"]);
  expect(result.current.recording).toBe(false);
  expect(result.current.elapsedSeconds).toBe(0);
  expect(stream.track.stopped).toBe(true);
});

test("drops a transcript that arrives after the user cancelled", async () => {
  const backend = new DictationRuntime();
  backend.holdTranscribe = true;
  const { result, onTranscript } = await ready(backend);

  await act(async () => result.current.toggle());
  await act(async () => result.current.toggle());
  await flush();
  expect(result.current.transcribing).toBe(true);

  await act(async () => result.current.cancel());
  await act(async () => {
    backend.releaseTranscribe();
  });
  await flush();

  expect(onTranscript).not.toHaveBeenCalled();
  expect(result.current.transcribing).toBe(false);
});

test("stops recording at the configured cap", async () => {
  const backend = new DictationRuntime();
  backend.status = { available: true, model: MODEL, maxAudioSeconds: 2 };
  const { result, onTranscript } = await ready(backend);

  await act(async () => result.current.toggle());
  await act(async () => {
    vi.advanceTimersByTime(3000);
  });
  await flush();

  expect(onTranscript).toHaveBeenCalled();
  expect(result.current.recording).toBe(false);
  expect(backend.methods).toContain("dictation/transcribe");
});

test("reports a denied microphone without recording", async () => {
  getUserMedia.mockRejectedValueOnce(
    new DOMException("permission denied", "NotAllowedError"),
  );
  const backend = new DictationRuntime();
  const { result } = await ready(backend);

  await act(async () => result.current.toggle());

  expect(result.current.recording).toBe(false);
  expect(result.current.error).toMatch(/Microphone access was denied/);
  expect(backend.methods).toEqual(["dictation/status"]);
});

test("reports an environment without a recorder", async () => {
  removeRecorderEnvironment();
  const backend = new DictationRuntime();
  const { result } = await ready(backend);

  await act(async () => result.current.toggle());

  expect(result.current.error).toMatch(/not available in this environment/);
  expect(result.current.recording).toBe(false);
});

test("surfaces an endpoint failure as an error instead of a transcript", async () => {
  const backend = new DictationRuntime();
  backend.transcribeError = new Error(
    "Voice input could not be transcribed. Check that the dictation endpoint is running, then try again.",
  );
  const { result, onTranscript } = await ready(backend);

  await act(async () => result.current.toggle());
  await act(async () => result.current.toggle());
  await flush();

  expect(result.current.error).toMatch(/could not be transcribed/);
  expect(onTranscript).not.toHaveBeenCalled();
  expect(result.current.transcribing).toBe(false);
});

test("ignores a silent clip without inserting anything", async () => {
  const backend = new DictationRuntime();
  backend.transcript = { text: "   ", model: MODEL };
  const { result, onTranscript } = await ready(backend);

  await act(async () => result.current.toggle());
  await act(async () => result.current.toggle());
  await flush();

  expect(result.current.error).toMatch(/No speech was detected/);
  expect(onTranscript).not.toHaveBeenCalled();
});

test("releases the microphone when the composer unmounts", async () => {
  const backend = new DictationRuntime();
  const { result, unmount } = await ready(backend);

  await act(async () => result.current.toggle());
  unmount();

  expect(stream.track.stopped).toBe(true);
  expect(backend.methods).toEqual(["dictation/status"]);
});
