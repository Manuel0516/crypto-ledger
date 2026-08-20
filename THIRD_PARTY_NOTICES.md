# Third-party notices

Crypto Ledger is distributed under the MIT License for its original source code. Third-party packages, plugins, country connections, fonts, icons, and other assets remain under their own terms.

## RP2

Crypto Ledger can use RP2 as an optional tax/reporting integration. The current boundary is:

- RP2 is a separately distributed dependency, not copied into this repository.
- Crypto Ledger invokes documented RP2 commands and plugin/country integration points when the user configures them.
- This repository does not relicense RP2 under the Crypto Ledger MIT License.
- A distribution that bundles RP2 must include RP2’s applicable copyright and Apache License 2.0 notices.
- RP2 is an independent project; Crypto Ledger does not imply endorsement, sponsorship, or affiliation.

References:

- [RP2 repository](https://github.com/eprbell/rp2)
- [RP2 Apache License 2.0 text](https://github.com/eprbell/rp2/blob/main/LICENSE)
- [RP2 developer guide](https://github.com/eprbell/rp2/blob/main/README.dev.md)
- [RP2 package metadata](https://pypi.org/project/rp2/)

## Dependency inventory

The API and web manifests are the source of truth for installed dependencies:

- [`api/requirements.txt`](api/requirements.txt) lists Python runtime dependencies, including the optional RP2 integration boundary.
- [`web/package.json`](web/package.json) lists JavaScript runtime and build dependencies.

Before publishing a release artifact, generate and review a complete dependency/license inventory for the exact lockfiles and bundled files. Keep each dependency’s required notices with a binary or source distribution when its license requires it.

## Release hygiene

Do not include personal wallet addresses, API credentials, backup keys, database files, exports, or unredacted screenshots/videos in a public release. Review optional plugin packages separately: their country data, code, and license terms may differ from RP2 and from Crypto Ledger.

This notice records the project’s current integration intent and is not legal advice. Obtain a legal review before redistributing a bundle containing RP2, plugins, or other third-party artifacts.
