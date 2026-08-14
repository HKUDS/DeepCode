import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "deepcode.desktop.projectDisclosure.v2";

type DisclosureOverrides = Record<string, boolean>;

function readDisclosureOverrides(): DisclosureOverrides {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
    if (typeof stored !== "object" || stored === null || Array.isArray(stored)) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(stored).filter(
        (entry): entry is [string, boolean] => typeof entry[1] === "boolean",
      ),
    );
  } catch {
    return {};
  }
}

export function useProjectDisclosure(activeGroupKey: string | null) {
  const [overrides, setOverrides] = useState(readDisclosureOverrides);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(overrides));
    } catch {
      // Disclosure state is a convenience and must not block navigation.
    }
  }, [overrides]);

  const toggle = useCallback((key: string) => {
    setOverrides((current) => ({
      ...current,
      [key]: !(current[key] ?? key === activeGroupKey),
    }));
  }, [activeGroupKey]);

  const expand = useCallback((key: string) => {
    setOverrides((current) =>
      current[key] === true ? current : { ...current, [key]: true },
    );
  }, []);

  const isExpanded = useCallback(
    (key: string) => overrides[key] ?? key === activeGroupKey,
    [activeGroupKey, overrides],
  );

  return { expand, isExpanded, toggle };
}
