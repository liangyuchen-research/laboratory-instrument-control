"""Read spectra exported by the Raspberry Pi acquisition controller.

Measurement names contain a timestamp followed by voltage, on-time, off-time,
cycles, conductivity and five metal concentrations. Background files end in
``_BG.txt`` and are not measurement rows. Intensities are already raw (mode 1)
or background-subtracted (mode 2); the reader does not subtract them again.
"""

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


class SpectralDataReader:
    columns = ["Time", "Voltage", "Ontime", "Offtime", "Cycle", "Conductivity",
               "Metal1", "Metal2", "Metal3", "Metal4", "Metal5", "Run"]

    def __init__(self, txt_files):
        self.txt_files = [Path(name) for name in txt_files]

    def Data_transfer(self):
        """Return aligned condition, intensity and grouping tables for plotting."""
        measurements = []
        for path in self.txt_files:
            if path.stem.endswith("_BG"):
                continue
            parts = path.stem.split("_")
            if len(parts) != 11:
                raise ValueError(f"Unsupported spectrum filename: {path.name}")
            try:
                timestamp = datetime.strptime(parts[0], "%Y%m%d-%H%M%S%f")
                parameters = [float(value) for value in parts[1:]]
                values = np.loadtxt(path, delimiter="\t", ndmin=2)
            except (ValueError, OSError) as exc:
                raise ValueError(f"Cannot read spectrum {path.name}: {exc}") from exc
            if not np.isfinite(parameters).all():
                raise ValueError(f"Non-finite filename parameters in {path.name}")
            if values.shape[1] != 2 or not len(values) or not np.isfinite(values).all():
                raise ValueError(f"Expected finite wavelength/intensity pairs in {path.name}")
            if np.any(np.diff(values[:, 0]) <= 0):
                raise ValueError(f"Wavelengths must be strictly increasing in {path.name}")
            measurements.append((timestamp, path, parameters, values))
        if not measurements:
            raise ValueError("No measurement spectra were found; background files are excluded")

        measurements.sort(key=lambda item: (item[0], str(item[1])))
        first_time = measurements[0][0]
        wavelengths = measurements[0][3][:, 0]
        rows, spectra = [], []
        for timestamp, path, parameters, values in measurements:
            if values.shape[0] != len(wavelengths) or not np.allclose(
                    values[:, 0], wavelengths, rtol=0, atol=1e-6):
                raise ValueError(f"Wavelength grid differs in {path.name}")
            rows.append([(timestamp - first_time).total_seconds(), *parameters, 1])
            spectra.append(values[:, 1])
        conditions = pd.DataFrame(rows, columns=self.columns)
        data = pd.DataFrame(spectra, columns=wavelengths.astype(str))
        groups = pd.Series({key: indices.tolist() for key, indices in
                            conditions.groupby(self.columns, sort=False).indices.items()})
        return conditions, data, groups
