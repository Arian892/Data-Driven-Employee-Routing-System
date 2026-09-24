import React, { useEffect, useRef, useState } from 'react';
import { MapPin, Search, Loader2, CheckCircle } from 'lucide-react';
import { InteractiveMap } from '../shared/InteractiveMap';
import { cachedAddress, reverseGeocode } from '../../services/geocode';

export interface MapLocation {
  address: string;
  lat: number;
  lng: number;
  pinned: boolean;
}

interface Suggestion {
  name: string;
  lat: number;
  lng: number;
}

interface MapLocationPickerProps {
  title: string;
  description: string;
  accentHex: string;
  value: MapLocation;
  onChange: (next: MapLocation) => void;
  /** Extra fixed marker (e.g. the office) shown for context + route. */
  origin?: { label: string; lat: number; lng: number } | null;
  height?: string;
}

// Forward geocoding via OpenStreetMap Nominatim (free, no API key).
const geocodeUrl = (query: string) =>
  `https://nominatim.openstreetmap.org/search?format=json&limit=6&countrycodes=bd&q=${encodeURIComponent(query)}`;

const isCoordinateString = (text: string) => /^-?\d+(\.\d+)?,\s*-?\d+(\.\d+)?$/.test(text);

export const MapLocationPicker: React.FC<MapLocationPickerProps> = ({
  title,
  description,
  accentHex,
  value,
  onChange,
  origin = null,
  height = '340px',
}) => {
  const [query, setQuery] = useState(value.address);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [searching, setSearching] = useState(false);
  const editingRef = useRef(false);
  // Latest coordinates the user pinned, so a slow background reverse-geocode
  // can't overwrite a newer click.
  const lastPinnedRef = useRef<{ lat: number; lng: number } | null>(null);

  // Keep the search box in sync when the location is set from the map or a suggestion.
  useEffect(() => {
    if (!editingRef.current) setQuery(value.address);
  }, [value.address]);

  // Debounced geocoding search while the user is typing.
  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed || !editingRef.current || isCoordinateString(trimmed)) {
      setSuggestions([]);
      return;
    }
    setSearching(true);
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 8000);
    const id = window.setTimeout(async () => {
      try {
        const res = await fetch(geocodeUrl(trimmed), { signal: ctrl.signal });
        const data: Array<{ display_name: string; lat: string; lon: string }> = await res.json();
        setSuggestions(
          (Array.isArray(data) ? data : []).map((item) => ({
            name: item.display_name,
            lat: parseFloat(item.lat),
            lng: parseFloat(item.lon),
          })),
        );
      } catch {
        setSuggestions([]);
      } finally {
        clearTimeout(timer);
        setSearching(false);
      }
    }, 450);
    return () => window.clearTimeout(id);
  }, [query]);

  const pickSuggestion = (s: Suggestion) => {
    editingRef.current = false;
    setQuery(s.name);
    setSuggestions([]);
    onChange({ address: s.name, lat: s.lat, lng: s.lng, pinned: true });
  };

  const handleMapSelect = (lat: number, lng: number) => {
    editingRef.current = false;
    setSuggestions([]);
    lastPinnedRef.current = { lat, lng };
    // Pin immediately (coordinate fallback) so the click always works even if
    // the geocoder stalls or rate-limits; upgrade the address in the background.
    const fallback = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
    onChange({ address: cachedAddress(lat, lng) ?? fallback, lat, lng, pinned: true });
    reverseGeocode(lat, lng).then((address) => {
      const cur = lastPinnedRef.current;
      if (cur && cur.lat === lat && cur.lng === lng) {
        onChange({ address, lat, lng, pinned: true });
      }
    });
  };

  return (
    <div>
      <div className="flex items-center gap-2 mb-1">
        <MapPin className="w-4 h-4" style={{ color: accentHex }} />
        <h3 className="text-sm font-semibold text-foreground" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
          {title}
        </h3>
      </div>
      <p className="text-xs text-muted-foreground mb-4">{description}</p>

      {/* Search box */}
      <div className="relative mb-3">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
        <input
          type="text"
          value={query}
          onChange={(e) => {
            editingRef.current = true;
            setQuery(e.target.value);
          }}
          onFocus={() => {
            editingRef.current = true;
          }}
          onBlur={() => {
            editingRef.current = false;
            window.setTimeout(() => setSuggestions([]), 200);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && suggestions.length > 0) {
              e.preventDefault();
              pickSuggestion(suggestions[0]);
            }
          }}
          placeholder="Search your location (e.g. Banani, Dhaka)…"
          className="w-full pl-9 pr-9 py-3 rounded-lg border border-border bg-muted text-foreground placeholder:text-muted-foreground text-sm focus:outline-none focus:border-sky-600/40 dark:focus:border-sky-400/40 transition"
        />
        {searching && (
          <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 animate-spin text-muted-foreground" />
        )}

        {suggestions.length > 0 && (
          <div className="absolute z-20 mt-1 w-full rounded-lg border border-border bg-card shadow-xl overflow-hidden">
            {suggestions.map((s, i) => (
              <button
                key={i}
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pickSuggestion(s)}
                className="w-full text-left px-4 py-2.5 text-xs text-foreground hover:bg-muted transition flex items-start gap-2 border-b border-border last:border-0"
              >
                <MapPin className="w-3 h-3 mt-0.5 flex-shrink-0 text-muted-foreground" />
                <span className="line-clamp-2">{s.name}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      <InteractiveMap
        center={[value.lat, value.lng]}
        zoom={value.pinned ? 14 : 12}
        onLocationSelect={handleMapSelect}
        markers={[
          ...(value.pinned
            ? [{ position: [value.lat, value.lng] as [number, number], label: title, color: accentHex }]
            : []),
          ...(origin
            ? [{ position: [origin.lat, origin.lng] as [number, number], label: origin.label, color: '#0EA5E9' }]
            : []),
        ]}
        showRoute={value.pinned && !!origin}
        height={height}
      />

      {value.pinned && (
        <p className="text-xs text-emerald-600 dark:text-emerald-400 mt-2 flex items-center gap-1.5">
          <CheckCircle className="w-3 h-3" /> {title} pinned on map
        </p>
      )}
    </div>
  );
};
