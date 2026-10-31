import { useMediaQuery } from "./useMediaQuery";

export function useSystemDarkMode(): boolean {
  return useMediaQuery("(prefers-color-scheme: dark)");
}
