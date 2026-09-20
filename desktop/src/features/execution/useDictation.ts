import { useCallback, useEffect, useRef, useState } from "react";

import type { DictationStatusResult } from "../../generated/app-server";
import type { ClientRuntime } from "../../rpc/contracts";

/**
 * Push-to-talk dictation for the composer.
 *
 * The audio never becomes prompt text by itself: the hook records, uploads the
 * clip, and hands the transcript to `onTranscript`, so the caller decides where
 * it lands (the composer inserts it at the caret). Three behaviours are worth
 * knowing before changing anything here:
 *
 * - Nothing is uploaded when dictation is not configured. `dictation/status`
 *   answers `available: false` and the caller renders no microphone at all.
 * - Recording is local until the user stops it. `cancel()` discards the clip
 *   without a request, which is what Escape does.
 * - A transcript that arrives after `cancel()` is dropped, so a slow endpoint
 *   cannot paste text the user already abandoned.
 */

/** Containers to try, best first. Chromium records webm, WKWebView only mp4. */
const MIME_CANDIDATES = ["audio/webm", "audio/ogg", "audio/mp4"] as const;

const RECORDING_UNAVAILABLE =
  "Dictation recording is not available in this environment.";

export interface UseDictationOptions {
  runtime: ClientRuntime;
  /** Receives the transcript. Called only for non-empty text. */
  onTranscript(text: string): void;
}

export interface UseDictationResult {
  /** True only when the app server reports a configured endpoint. */
  available: boolean;
  recording: boolean;
  transcribing: boolean;
  elapsedSeconds: number;
  error: string | null;
  /** Start recording, or stop and transcribe when already recording. */
  toggle(): void;
  /** Stop recording and throw the clip away without transcribing it. */
  cancel(): void;
}

function messageOf(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

function errorName(cause: unknown): string {
  if (typeof cause !== "object" || cause === null || !("name" in cause)) return "";
  return String((cause as { name: unknown }).name);
}

function microphoneMessage(cause: unknown): string {
  const name = errorName(cause);
  if (name === "NotAllowedError" || name === "SecurityError") {
    return (
      "Microphone access was denied. Allow it for DeepCode in your system " +
      "settings, then try again."
    );
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError") {
    return "No microphone was found on this machine.";
  }
  return `The microphone could not be opened (${messageOf(cause)}).`;
}

/** Strip codec parameters: the endpoint dispatches on the container alone. */
function canonicalMimeType(mimeType: string): string {
  return mimeType.split(";")[0].trim().toLowerCase();
}

function preferredMimeType(): string | null {
  const Recorder = globalThis.MediaRecorder;
  if (typeof Recorder?.isTypeSupported !== "function") return null;
  for (const candidate of MIME_CANDIDATES) {
    if (Recorder.isTypeSupported(candidate)) return candidate;
  }
  return null;
}

/**
 * Base64 in 32 KiB chunks: `String.fromCharCode(...bytes)` on a whole clip
 * overflows the argument limit and would throw on a long recording.
 */
async function blobToBase64(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = "";
  const chunk = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunk) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunk));
  }
  return btoa(binary);
}

export function useDictation({
  runtime,
  onTranscript,
}: UseDictationOptions): UseDictationResult {
  const [status, setStatus] = useState<DictationStatusResult | null>(null);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const mimeTypeRef = useRef<string>("");
  const discardRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startedAtRef = useRef(0);
  const maxSecondsRef = useRef<number | null>(null);
  const transcriptRef = useRef(onTranscript);

  useEffect(() => {
    transcriptRef.current = onTranscript;
  }, [onTranscript]);

  // The capability is a config read; asking once per mount is enough, and a
  // failure (including a server that predates the method) means "no mic".
  useEffect(() => {
    let cancelled = false;
    void runtime
      .request("dictation/status", {})
      .then((result) => {
        if (cancelled) return;
        setStatus(result);
      })
      .catch(() => {
        if (!cancelled) setStatus(null);
      });
    return () => {
      cancelled = true;
    };
  }, [runtime]);

  const stopTimer = useCallback(() => {
    if (timerRef.current !== null) clearInterval(timerRef.current);
    timerRef.current = null;
  }, []);

  const releaseStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  const finish = useCallback(async () => {
    stopTimer();
    releaseStream();
    setRecording(false);
    setElapsedSeconds(0);
    const chunks = chunksRef.current;
    chunksRef.current = [];
    const recorder = recorderRef.current;
    recorderRef.current = null;
    const mimeType = canonicalMimeType(
      recorder?.mimeType || mimeTypeRef.current || chunks[0]?.type || "",
    );
    if (discardRef.current) {
      discardRef.current = false;
      return;
    }
    if (!chunks.length || !mimeType) {
      setError("The recording was empty; nothing was transcribed.");
      return;
    }
    setTranscribing(true);
    try {
      const audio = await blobToBase64(new Blob(chunks, { type: mimeType }));
      const result = await runtime.request("dictation/transcribe", {
        audio,
        mimeType,
      });
      // A clip the user cancelled while it was uploading must not paste text.
      if (discardRef.current) {
        discardRef.current = false;
        return;
      }
      const text = result.text.trim();
      if (text) transcriptRef.current(text);
      else setError("No speech was detected in the recording.");
    } catch (cause) {
      if (!discardRef.current) setError(messageOf(cause));
    } finally {
      discardRef.current = false;
      setTranscribing(false);
    }
  }, [releaseStream, runtime, stopTimer]);

  const stop = useCallback(() => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.stop();
      return;
    }
    void finish();
  }, [finish]);

  const available = status?.available === true;

  const start = useCallback(async () => {
    // The caller renders no microphone while dictation is unavailable, so this
    // guard only matters to a host that calls the hook directly.
    if (!available) return;
    setError(null);
    const media = globalThis.navigator?.mediaDevices;
    if (!media?.getUserMedia || typeof globalThis.MediaRecorder !== "function") {
      setError(RECORDING_UNAVAILABLE);
      return;
    }
    let stream: MediaStream;
    try {
      stream = await media.getUserMedia({ audio: true });
    } catch (cause) {
      setError(microphoneMessage(cause));
      return;
    }
    const mimeType = preferredMimeType();
    let recorder: MediaRecorder;
    try {
      recorder = new MediaRecorder(
        stream,
        mimeType ? { mimeType } : undefined,
      );
    } catch (cause) {
      stream.getTracks().forEach((track) => track.stop());
      setError(`Recording could not start (${messageOf(cause)}).`);
      return;
    }
    streamRef.current = stream;
    recorderRef.current = recorder;
    mimeTypeRef.current = mimeType ?? "";
    chunksRef.current = [];
    discardRef.current = false;
    recorder.ondataavailable = (event: BlobEvent) => {
      if (event.data?.size) chunksRef.current.push(event.data);
    };
    recorder.onstop = () => {
      void finish();
    };
    recorder.start();
    setRecording(true);
    setTranscribing(false);
    setElapsedSeconds(0);
    startedAtRef.current = Date.now();
    stopTimer();
    timerRef.current = setInterval(() => {
      const seconds = Math.floor((Date.now() - startedAtRef.current) / 1000);
      setElapsedSeconds(seconds);
      const max = maxSecondsRef.current;
      const active = recorderRef.current;
      // The configured cap is enforced here rather than trusted to the
      // recorder: a long clip would exceed the upload ceiling otherwise.
      if (max !== null && seconds >= max && active?.state === "recording") {
        active.stop();
      }
    }, 1000);
  }, [available, finish, stopTimer]);

  const cancel = useCallback(() => {
    discardRef.current = true;
    stopTimer();
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.stop();
      return;
    }
    recorderRef.current = null;
    chunksRef.current = [];
    releaseStream();
    setRecording(false);
    setTranscribing(false);
    setElapsedSeconds(0);
  }, [releaseStream, stopTimer]);

  useEffect(() => {
    maxSecondsRef.current = status?.available
      ? status.maxAudioSeconds
      : null;
  }, [status]);

  useEffect(
    () => () => {
      discardRef.current = true;
      if (timerRef.current !== null) clearInterval(timerRef.current);
      timerRef.current = null;
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== "inactive") recorder.stop();
      recorderRef.current = null;
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    },
    [],
  );

  const toggle = useCallback(() => {
    if (recording) stop();
    else void start();
  }, [recording, start, stop]);

  return {
    available,
    recording,
    transcribing,
    elapsedSeconds,
    error,
    toggle,
    cancel,
  };
}
