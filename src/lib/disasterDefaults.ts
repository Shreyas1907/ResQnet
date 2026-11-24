export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "";

export const DEFAULT_LOCATION: Record<string, { latitude: number; longitude: number }> = {
  flood: { latitude: 23.8103, longitude: 90.4125 },
  earthquake: { latitude: 35.6762, longitude: 139.6503 },
  tsunami: { latitude: 38.3, longitude: 142.4 },
  drought: { latitude: 2.0469, longitude: 45.3182 },
  fire: { latitude: -12.5, longitude: 28.5 },
};
