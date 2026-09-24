import React from 'react';
import { useAddress } from '../../services/geocode';

/**
 * Plain-text address for a coordinate pair. Resolves instantly from the
 * localStorage geocode cache (home addresses land here when the employee pins
 * them on the map), otherwise looks it up via Photon in the background. While
 * resolving it shows a muted placeholder rather than repeating the coordinates
 * that are usually printed underneath.
 *
 * Pass `className` to style the resolved text (defaults to `text-stone-700`).
 */
export const AddressText: React.FC<{ lat?: number | null; lng?: number | null; className?: string }> = ({
  lat,
  lng,
  className,
}) => {
  const address = useAddress(lat, lng);
  // `block` (not the default inline) so a caller's `truncate` class actually
  // takes effect — Tailwind's truncate (overflow/text-overflow/nowrap) is a
  // no-op on inline elements, so a long address would otherwise overflow its
  // row instead of ellipsizing, pushing sibling content out of view.
  if (lat == null || lng == null) return <span className="block text-muted-foreground">No location set</span>;
  if (!address) return <span className="block text-muted-foreground">Locating address…</span>;
  return <span className={`block ${className ?? 'text-foreground'}`}>{address}</span>;
};
