import React, { useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

delete (L.Icon.Default.prototype as any)._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
});

interface MapPickerProps {
  center: [number, number];
  zoom?: number;
  onLocationSelect?: (lat: number, lng: number) => void;
  markers?: Array<{
    position: [number, number];
    label: string;
    color?: string;
    /** Text shown inside the pin, e.g. '1', '2', ... Defaults to the marker's 1-based index. */
    code?: string | number;
    /** 'office' renders a distinct rounded badge; 'mine' renders a larger, highlighted pin; 'start' is the vehicle's own starting position (an "S" pin, muted so it doesn't compete with numbered stops). */
    variant?: 'stop' | 'office' | 'mine' | 'start';
  }>;
  showRoute?: boolean;
  /**
   * Road-following path from the solver, as [lat, lng] pairs. When present and
   * `showRoute` is on, it is drawn as a solid line instead of the dashed
   * marker-to-marker guess — the actual streets the vehicle drives. Omitted or
   * null (e.g. the backend ran the haversine fallback) keeps the dashed line,
   * which is honest about being an approximation.
   */
  routeGeometry?: [number, number][] | null;
  /**
   * Fit the view to include every marker, instead of the fixed `center`/`zoom`.
   * Use for route displays where a marker (e.g. the office) can be far enough
   * from `center` to fall outside a fixed zoom level — without this, that pin
   * exists on the map but is scrolled off-screen. Leave off for pickers where
   * the person controls pan/zoom themselves (re-fitting on every pin change
   * would fight their own zooming).
   */
  fitToMarkers?: boolean;
  height?: string;
  /**
   * Mount the map only once it scrolls into view. Use on pages that render
   * many route cards, so a whole list of Leaflet maps isn't downloading OSM
   * tiles at once. Off-screen cards show a lightweight placeholder instead.
   */
  lazy?: boolean;
}

// A briefcase glyph, not an emoji — emoji rendering (esp. building emoji)
// varies noticeably across OS/browser, so the office marker uses a crisp,
// consistent inline SVG instead.
const BRIEFCASE_SVG = `
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#1E1B4B" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
    <rect x="2" y="7" width="20" height="14" rx="2"></rect>
    <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"></path>
  </svg>
`;

const createColoredIcon = (color: string, code: string | number = '', variant: 'stop' | 'office' | 'mine' | 'start' = 'stop') => {
  const isOffice = variant === 'office';
  const isMine = variant === 'mine';
  const size = isOffice ? 34 : isMine ? 36 : 30;
  const ringColor = isMine ? '#FACC15' : 'rgba(255,255,255,0.95)';
  const ringWidth = isMine ? 3 : 2;
  const glow = isMine
    ? '0 0 0 4px rgba(250,204,21,0.3), 0 2px 8px rgba(0,0,0,0.45)'
    : '0 2px 6px rgba(0,0,0,0.4)';
  const background = isOffice ? '#FFFFFF' : color;
  const content = isOffice ? BRIEFCASE_SVG : `<span style="color: white; font-weight: 700; font-size: 13px; font-family: Rajdhani, sans-serif; line-height: 1;">${code}</span>`;

  return L.divIcon({
    className: 'custom-marker',
    html: `
      <div style="
        width: ${size}px;
        height: ${size}px;
        border-radius: 50%;
        background-color: ${background};
        border: ${ringWidth}px solid ${ringColor};
        box-shadow: ${glow};
        display: flex;
        align-items: center;
        justify-content: center;
      ">
        ${content}
      </div>
    `,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
    popupAnchor: [0, -(size / 2 + 4)],
  });
};

export const InteractiveMap: React.FC<MapPickerProps> = ({
  center,
  zoom = 13,
  onLocationSelect,
  markers = [],
  showRoute = false,
  routeGeometry = null,
  fitToMarkers = false,
  height = '420px',
  lazy = false,
}) => {
  const mapRef = useRef<HTMLDivElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<L.Map | null>(null);
  const markersRef = useRef<L.Marker[]>([]);
  const polylineRef = useRef<L.Polyline | null>(null);
  const selectedMarkerRef = useRef<L.Marker | null>(null);
  // Eager maps mount immediately; lazy maps wait until they scroll into view.
  const [visible, setVisible] = useState(!lazy);

  useEffect(() => {
    if (!lazy || visible) return;
    const el = wrapperRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      entries => {
        if (entries.some(e => e.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      // Preload slightly before the card scrolls fully into view.
      { rootMargin: '200px' },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [lazy, visible]);

  useEffect(() => {
    if (!visible) return;
    if (!mapRef.current || mapInstanceRef.current) return;

    const map = L.map(mapRef.current, {
      zoomControl: true,
      attributionControl: false,
    }).setView(center, zoom);

    mapInstanceRef.current = map;

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
    }).addTo(map);

    L.control.attribution({ prefix: '© OpenStreetMap contributors' }).addTo(map);

    if (onLocationSelect) {
      map.on('click', (e: L.LeafletMouseEvent) => {
        const { lat, lng } = e.latlng;
        onLocationSelect(lat, lng);

        if (selectedMarkerRef.current) {
          selectedMarkerRef.current.remove();
        }
        const m = L.marker([lat, lng], {
          icon: createColoredIcon('#F59E0B'),
        }).addTo(map);
        m.bindPopup(`<div style="background:#ffffff;color:#0f172a;padding:8px 12px;border-radius:8px;font-size:12px;border:1px solid rgba(15,23,42,0.1)">
          <strong style="display:block;margin-bottom:2px">Selected Location</strong>
          <span style="color:#64748b;font-family:monospace">${lat.toFixed(5)}, ${lng.toFixed(5)}</span>
        </div>`).openPopup();
        selectedMarkerRef.current = m;
      });
    }

    return () => {
      if (mapInstanceRef.current) {
        mapInstanceRef.current.remove();
        mapInstanceRef.current = null;
      }
    };
  }, [visible]);

  useEffect(() => {
    if (mapInstanceRef.current) {
      mapInstanceRef.current.setView(center, zoom);
    }
  }, [center, zoom]);

  useEffect(() => {
    if (!mapInstanceRef.current) return;
    markersRef.current.forEach(m => m.remove());
    markersRef.current = [];

    markers.forEach((md, idx) => {
      const variant = md.variant ?? 'stop';
      const defaultColor = variant === 'office' ? '#10B981' : variant === 'mine' ? '#F59E0B' : variant === 'start' ? '#64748B' : '#475569';
      const marker = L.marker(md.position, {
        icon: createColoredIcon(md.color || defaultColor, md.code ?? idx + 1, variant),
        // Leaflet's default stacking is purely by latitude — on a route whose
        // stops span kilometres, the map zooms out far enough that a 'start'
        // marker sitting near a stop is only a couple of screen pixels away
        // from it, so whichever one Leaflet happens to draw on top hides the
        // other completely. 'start' is deliberately rare (one per leg) and
        // exactly the one riders need to be able to find, so it always wins.
        zIndexOffset: variant === 'start' ? 1000 : 0,
      }).addTo(mapInstanceRef.current!);

      marker.bindPopup(`<div style="background:#ffffff;color:#0f172a;padding:8px 12px;border-radius:8px;font-size:12px;border:1px solid rgba(15,23,42,0.1)">
        <strong style="display:block;margin-bottom:2px">${md.label}</strong>
        <span style="color:#64748b;font-family:monospace">${md.position[0].toFixed(5)}, ${md.position[1].toFixed(5)}</span>
      </div>`);

      markersRef.current.push(marker);
    });

    // A fixed center/zoom is framed around one point (e.g. the employee's own
    // stop) — a marker far from it, like the office, can exist on the map but
    // sit outside the visible viewport. Fitting to all markers guarantees
    // everything placed on the map is actually seen.
    if (fitToMarkers && markers.length > 1) {
      const bounds = L.latLngBounds(markers.map(m => m.position as L.LatLngExpression));
      mapInstanceRef.current.fitBounds(bounds, { padding: [32, 32], maxZoom: 15 });
    }
    // `visible` matters for lazy maps: the map instance (mapInstanceRef.current)
    // is only created once the card scrolls into view, which happens strictly
    // after the first render — without `visible` here, that transition doesn't
    // re-run this effect, so markers silently never get drawn (only the
    // geometry effect below re-runs on `visible`, which is why the route line
    // alone would show up with no markers on it).
  }, [markers, visible, fitToMarkers]);

  useEffect(() => {
    if (!mapInstanceRef.current) return;
    if (polylineRef.current) { polylineRef.current.remove(); polylineRef.current = null; }
    if (!showRoute) return;

    // Guard against a truncated or malformed geometry rather than handing
    // Leaflet a bad LatLng and blanking the whole map.
    const road = (routeGeometry || []).filter(
      p => Array.isArray(p) && p.length === 2 && Number.isFinite(p[0]) && Number.isFinite(p[1]),
    );

    if (road.length > 1) {
      polylineRef.current = L.polyline(road as L.LatLngExpression[], {
        color: '#475569',
        weight: 4,
        opacity: 0.9,
      }).addTo(mapInstanceRef.current);
    } else if (markers.length > 1) {
      // No geometry — dash it, so nobody mistakes a straight hop between stops
      // for a real driving path.
      polylineRef.current = L.polyline(markers.map(m => m.position as L.LatLngExpression), {
        color: '#475569',
        weight: 3,
        opacity: 0.8,
        dashArray: '8, 8',
      }).addTo(mapInstanceRef.current);
    }
  }, [markers, showRoute, routeGeometry, visible]);

  return (
    <div
      ref={wrapperRef}
      style={{
        height,
        width: '100%',
        borderRadius: '10px',
        overflow: 'hidden',
        border: '1px solid rgba(15,23,42,0.1)',
        zIndex: 0,
        position: 'relative',
      }}
    >
      {lazy && !visible ? (
        <div
          style={{
            height: '100%',
            width: '100%',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 6,
            background: '#F1F5F9',
          }}
        >
          <span style={{ fontSize: 20, opacity: 0.4 }}>🗺️</span>
          <span style={{ fontSize: 11, color: '#64748b', letterSpacing: '0.02em' }}>
            Map loads when visible
          </span>
        </div>
      ) : (
        <div
          ref={mapRef}
          style={{ height: '100%', width: '100%' }}
        />
      )}
    </div>
  );
};
