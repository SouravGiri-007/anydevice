/**
 * The signature visual: a slowly rotating vortex of light at the center of the
 * screen. It isn't decoration — it's the mechanism. Drops get pulled in;
 * content emerges from it on the receiving device.
 *
 * Built from two masked conic-gradient discs spinning in opposite directions
 * (transform-only, GPU-cheap). The surrounding stage adds `.absorbing` for the
 * one non-ambient moment: while a code is being generated the portal speeds up
 * and glows while the prompt is pulled into the center.
 */
export default function Portal() {
  return (
    <div className="portal" aria-hidden="true">
      <div className="portal-glow" />
      <div className="portal-vortex a" />
      <div className="portal-vortex b" />
      <div className="portal-ring" />
      <div className="portal-ring thin" />
    </div>
  );
}
