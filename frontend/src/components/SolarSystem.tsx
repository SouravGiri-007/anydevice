import {
  FileDocIcon,
  FileImageIcon,
  FileSheetIcon,
  FileTextIcon,
  FileVideoIcon,
  FileZipIcon,
} from "./icons";

interface PlanetSpec {
  icon: React.ReactNode;
  label: string;
  cls: string;
}

interface OrbitSpec {
  cls: string;
  planets: PlanetSpec[];
}

const ORBITS: OrbitSpec[] = [
  {
    cls: "orb-is",
    planets: [
      { icon: <FileVideoIcon />, label: "MP4", cls: "core-video" },
      { icon: <FileZipIcon />, label: "ZIP", cls: "core-zip core-south" },
    ],
  },
  {
    cls: "orb-doc",
    planets: [{ icon: <FileDocIcon />, label: "PDF", cls: "core-doc" }],
  },
  {
    cls: "orb-image",
    planets: [{ icon: <FileImageIcon />, label: "PNG", cls: "core-image" }],
  },
  {
    cls: "orb-sheet",
    planets: [{ icon: <FileSheetIcon />, label: "XLS", cls: "core-sheet" }],
  },
  {
    cls: "orb-text",
    planets: [{ icon: <FileTextIcon />, label: "TXT", cls: "core-text" }],
  },
];

export default function SolarSystem() {
  return (
    <div className="solar-scene" aria-hidden="true">
      <div className="solar-sun" />
      {ORBITS.map((o) => (
        <div key={o.cls} className={`solar-orbit ${o.cls}`}>
          <div className="solar-ring" />
          {o.planets.map((p) => (
            <div key={p.label} className={`solar-core ${p.cls}`}>
              {p.icon}
              <span className="solar-ext">{p.label}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}