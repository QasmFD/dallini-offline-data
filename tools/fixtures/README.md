# Public compatibility fixture

`baghdad-bumps-schema1.geojson` is an unchanged copy of the owner's public
`QasmFD/dallini-offline-data` `edits/bumps.geojson` snapshot observed at main
commit `625adc9f586925b1ee903ccc914093c7688c3600` on 29 October 2026.
SHA256: `6712d05c0bab77709a30a3fc2205c3a4fe0ee304a8875e624333dbac6e8c481a`.

It tests compatibility with the nine authored points and their exact metadata.
The fixture does not verify their OSM provenance, actual positions, physical
shape or current road conditions. It must never overwrite the live feed or
serve as a production seed. Later live revisions remain independent of this
immutable test input.
