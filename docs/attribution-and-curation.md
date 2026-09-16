# Attribution and curation

## Source groups

- The primary desktop interface, Raspberry Pi controller, and STM32 firmware came from the main instrument-control research directory supplied by the repository owner.
- The Arduino/RS485 sketch came from a separately contributor-named subdirectory. It is presented as a contributed variant. The private provenance ledger retains the exact original directory name, without asserting an unverified English name or sole ownership.
- The Xcos diagram came from the separate feedback-control model directory. Its original ZIP package and extracted source were both archived.
- HAL, CMSIS, and STM32 device support files retain their original STMicroelectronics and Arm notices. The licenses remain at their original positions under `firmware/stm32/Drivers/`.

## Public-source changes

All changes were made to copies. Original source files and their hashes remain in the private archive.

1. Translated comments, docstrings, and interface messages into English.
2. Renamed the desktop modules and Raspberry Pi entry point to descriptive English paths, updating imports and remote-path configuration.
3. Replaced local device credentials and workstation-specific paths with a local INI file and environment variables. The example configuration is intentionally blank for connection details.
4. Replaced position-dependent `config.txt` reads and `eval` of wavelength lists with named INI values and JSON parsing.
5. Removed the earlier `send_to_pi` definition that was already shadowed by a later method of the same name. The effective sender remains and quotes remote command arguments with `shlex.quote`.
6. Retained the GUI acquisition-history file on close rather than deleting it.
7. Parameterized serial device, baud rate, physical trigger pin, spectrometer library path, and acquisition output directory.
8. Formatted Python source and normalized exported source line endings without redesigning pulse or acquisition algorithms.

## Material retained privately

The complete 202-file source archive includes compiled executables, bytecode caches, STM32 debug output, the vendor shared library, original credentials/configuration, acquisition-history logs, alternate contributed Python/GUI variants, the older Raspberry Pi script, and the original model ZIP. Nothing was deleted from the source directories.

The selected public implementation follows the main STM32 control path. Alternate protocols are not mixed into that path. The contributed Arduino sketch is kept in its own directory to make its different provenance and interface explicit.

## Validation boundaries

Python syntax, source language, configuration parsing, numerical protocol fields, archive integrity, vendor-file hashes, and the Xcos ZIP/XML structure were checked. The protocol tests replace both serial and GPIO with in-memory fakes. No hardware was accessed. STM32 compilation, Arduino compilation, graphical launch, SSH connectivity, vendor SDK loading, pulse timing, spectrum acquisition, and full feedback simulation were not validated.

No publication result, measured latency, hardware accuracy, author name, or software/data license was invented during this organization work.
