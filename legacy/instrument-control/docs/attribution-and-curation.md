# Attribution and source notes

## Source groups

- The primary desktop interface, Raspberry Pi controller, and STM32 firmware belong to the instrument-control research source collection.
- The Arduino/RS485 sketch is a contributed variant from a separate source subdirectory. The private provenance ledger retains its original contributor directory name.
- The Xcos diagram came from the separate feedback-control model directory. Its original ZIP package and extracted source were both archived.
- HAL, CMSIS, and STM32 device support files retain their original STMicroelectronics and Arm notices. The licenses remain at their original positions under `firmware/stm32/Drivers/`.

## Repository changes

All changes were made to copies. Original source files and their hashes remain in the private archive.

1. Translated comments, docstrings, and interface messages into English.
2. Renamed the desktop modules and Raspberry Pi entry point to descriptive English paths, updating imports and remote-path configuration.
3. Replaced local device credentials and workstation-specific paths with a local INI file and environment variables. The example configuration is intentionally blank for connection details.
4. Replaced position-dependent `config.txt` reads and `eval` of wavelength lists with named INI values and JSON parsing.
5. Removed the earlier `send_to_pi` definition that was already shadowed by a later method of the same name. The effective sender remains and quotes remote command arguments with `shlex.quote`.
6. Retained the GUI acquisition-history file on close rather than deleting it.
7. Parameterized serial device, baud rate, physical trigger pin, spectrometer library path, and acquisition output directory.
8. Formatted Python source and normalized exported source line endings without redesigning pulse or acquisition algorithms.
9. Treat nonzero remote-controller exit codes as acquisition failures, preventing a failed run from triggering a download of previous results. The worker reports completion to the GUI even after an exception.
10. Align the desktop reader with the current Raspberry Pi export filenames, reject incompatible wavelength grids, retain signed intensities, and avoid repeated background subtraction. Use full timestamp precision to avoid overwriting fast acquisitions.
11. Propagate UART and worker failures, release waiting pulse threads, and stop/close UART in cleanup. Reject non-finite or overflowing command fields, normalizing hundredth-millisecond carry without changing valid decoded durations.
12. Remove unused PyQt imports, use available Tk themes and portable colors, hide unimplemented designer controls, and label window closing accurately. Preserve the original remote-cancellation limitation. Correct the host timeout unit conversion with a rounded-up acquisition-aware watchdog budget and reject unrepresentable durations before connecting to hardware.
13. Add a standalone STM32 build recipe and check both legacy firmware variants by compilation only. The Arduino variant uses the portable Arduino header name.

## Material retained privately

The complete 202-file source archive includes compiled executables, bytecode caches, STM32 debug output, the vendor shared library, original credentials/configuration, acquisition-history logs, alternate contributed Python/GUI variants, the older Raspberry Pi script, and the original model ZIP. Nothing was deleted from the source directories.

The selected public implementation follows the main STM32 control path. Alternate protocols are not mixed into that path. The contributed Arduino sketch is kept in its own directory to make its different provenance and interface explicit.

## Validation boundaries

Python syntax, source language, configuration parsing, numerical protocol fields, archive integrity, vendor-file hashes, acquisition parsing, failure cleanup and the Xcos ZIP/XML structure were checked. The protocol tests replace both serial and GPIO with in-memory fakes. The STM32 and contributed Arduino sources both compiled successfully. No hardware was accessed. Graphical operation with real instruments, SSH connectivity, vendor SDK loading, pulse timing, spectrum acquisition, and full feedback simulation were not validated.

Hardware behavior and research results require validation on the original instrument.
