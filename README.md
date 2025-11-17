# Qubic Static

This repository provides static data and assets related to Qubic blockchain. It serves as a single, reliable source of structured data for developers, explorers, wallets, dashboards, and analysis tools.

## Data Categories

### General Data (Shared)
Available at: **https://static.qubic.org/v1/general/data/**

- **Smart contracts** — names, indexes, procedures, GitHub source links, and addresses
- **Exchanges** — known trading platforms and their Qubic addresses
- **Tokens** — additional token information
- **Address labels** — relevant Qubic addresses

### Product-Specific Data

#### Wallet App
Available at: **https://static.qubic.org/v1/wallet-app/**

- **DApp Explorer** - Curated list of DApps with multilingual support (7 languages: EN, DE, ES, FR, RU, TR, ZH)
- Mobile wallet configuration files

> **⚠️ IMPORTANT WARNING**
>
> The `wallet-app` data is **NOT intended for public consumption or third-party use**. This data is maintained **exclusively for the official Qubic Wallet application**.
>
> - ❌ **No stability guarantee** - Breaking changes may occur at any time without notice
> - ❌ **No support** - We do not provide support for third-party usage
> - ❌ **No responsibility** - We are NOT responsible for issues arising from third-party use
> - ❌ **No API contract** - Structure and content may change without warning
>
> **If you need this data:** Fork the repository and maintain your own copy. You assume all risks.
>
> See [products/wallet-app/README.md](products/wallet-app/README.md) for full details.

_Note: Currently, wallet-app is the only product-specific data. Additional products may be added in the future as the ecosystem grows._

## Available Files

### General Data Files
Base URL: `https://static.qubic.org/v1/general/data/`

- **Smart Contracts**
  - [smart_contracts.json](https://static.qubic.org/v1/general/data/smart_contracts.json)
  - [smart_contracts.min.json](https://static.qubic.org/v1/general/data/smart_contracts.min.json)

- **Exchanges**
  - [exchanges.json](https://static.qubic.org/v1/general/data/exchanges.json)
  - [exchanges.min.json](https://static.qubic.org/v1/general/data/exchanges.min.json)

- **Tokens**
  - [tokens.json](https://static.qubic.org/v1/general/data/tokens.json)
  - [tokens.min.json](https://static.qubic.org/v1/general/data/tokens.min.json)

- **Address Labels**
  - [address_labels.json](https://static.qubic.org/v1/general/data/address_labels.json)
  - [address_labels.min.json](https://static.qubic.org/v1/general/data/address_labels.min.json)

- **Bundle (All files combined)**
  - [bundle.json](https://static.qubic.org/v1/general/data/bundle.json)
  - [bundle.min.json](https://static.qubic.org/v1/general/data/bundle.min.json)

- **Version Tracking**
  - [version.json](https://static.qubic.org/v1/general/data/version.json) - Contains file hashes and sizes for cache invalidation

### Wallet App Files
Base URL: `https://static.qubic.org/v1/wallet-app/`

- **DApp Explorer**
  - [dapps/dapps.json](https://static.qubic.org/v1/wallet-app/dapps/dapps.json) - Curated DApp catalog
  - Locales: `dapps/locales/{lang}.json` (en, de, es, fr, ru, tr, zh)
  - **Want to add your DApp?** See [How to Contribute](#how-to-contribute) section below

## Environments & URL Structure

This repository supports three deployment environments:

- **Production**: `https://static.qubic.org/v1/` - Stable releases (e.g., v1.2.3)
- **Staging**: `https://static.qubic.org/staging/v1/` - Release candidates (e.g., v1.2.3-rc.1)
- **Dev**: `https://static.qubic.org/dev/v1/` - Development testing

### URL Format

```
Production: https://static.qubic.org/v1/{product}/{path-to-file}
Staging:    https://static.qubic.org/staging/v1/{product}/{path-to-file}
Dev:        https://static.qubic.org/dev/v1/{product}/{path-to-file}
```

### Examples

**General Data:**
```
https://static.qubic.org/v1/general/data/smart_contracts.json
https://static.qubic.org/staging/v1/general/data/smart_contracts.json
```

**Wallet App:**
```
https://static.qubic.org/v1/wallet-app/dapps/dapps.json
https://static.qubic.org/v1/wallet-app/dapps/locales/en.json
```

### Version Tracking & Cache Invalidation

Each product includes a `version.json` file for efficient cache management:

```javascript
// Fetch version metadata
const versionData = await fetch('https://static.qubic.org/v1/wallet-app/version.json')
  .then(r => r.json());

// Compare file hashes with cached versions
for (const [filename, metadata] of Object.entries(versionData.files)) {
  if (cachedHashes[filename] !== metadata.hash) {
    // Hash changed → download new file
    await downloadFile(filename);
  }
}
```

The `version.json` contains SHA-256 hashes and file sizes for all files, enabling apps to:
- Only download changed files
- Properly invalidate caches
- Verify file integrity

## Repository Structure

```
data/                              # General/shared source data
  ├── smart_contracts.json
  ├── exchanges.json
  ├── tokens.json
  └── address_labels.json

products/                          # Product-specific source data
  └── wallet-app/
      └── dapps/
          ├── dapps.json
          └── locales/

scripts/                           # Build and update utilities
  ├── build_dist.py               # Build distribution files
  └── update_smart_contracts.py   # Update SC data from GitHub

.github/workflows/                 # CI/CD automation
  ├── commitlint.yml              # Commit message validation
  ├── lint-pr.yml                 # PR title validation
  └── deploy.yml                  # Automated deployments
```

## CI/CD & Releases

This repository uses GitHub Actions with semantic-release for automated versioning and deployments.

### Commit Convention

Follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` - New features (triggers MINOR version bump)
- `fix:` - Bug fixes (triggers PATCH version bump)
- `docs:` - Documentation changes (no version bump)
- `chore:` - Maintenance tasks (no version bump)

**Examples:**
```
feat: add proposalUrl field to smart contracts
fix: correct address calculation for contract index 166
docs: update README with new structure
```

### Deployment Workflow

1. **Dev** - Push to `dev` branch → deploys to dev environment (no versioning)
2. **Staging** - Push to `staging` branch → creates RC tag & GitHub pre-release → deploys to staging
3. **Production** - Push to `main` branch → creates version tag & GitHub release → deploys to production

### Release Workflow

1. **Development** - Make changes in feature branches using conventional commit messages
2. **Dev Testing** - Merge to `dev` branch → auto-deploys to dev environment
3. **Staging** - Merge to `staging` branch → semantic-release creates RC tag & deploys
4. **Testing** - Test on staging environment
5. **Production** - Merge `staging` to `main` → creates release & deploys to production

Semantic-release automatically:
- Analyzes commit messages
- Determines version numbers
- Creates GitHub releases
- Generates changelogs
- Triggers deployments

## How to Contribute

Contributions are welcome! Here's how you can help:

### Adding Data

**DApps for Wallet Explorer** 
- Add your DApp to the curated explorer list in `products/wallet-app/dapps/dapps.json`
- Include title and description translations in all 7 languages: `products/wallet-app/dapps/locales/*.json` (en, de, es, fr, ru, tr, zh)
- Provide app URL, icon (set "" when not available)
- An example can be seen here https://github.com/qubic/static/pull/24

**Exchange Addresses**
- Add new exchange addresses to `data/exchanges.json`
- Include exchange name and Qubic address

**Token Information**
- Add complementary token data to `data/tokens.json`
- Reference tokens by name (not all tokens need to be listed, only those with additional metadata)

**Address Labels**
- Add relevant Qubic addresses to `data/address_labels.json`
- Include descriptive labels for known addresses

**Smart Contract Custom Data**
- Add custom fields or metadata to existing smart contracts in `data/smart_contracts.json`
- Note: Core fields (name, contractIndex, address, procedures) are auto-generated. Open an Issue for corrections to these fields rather than submitting PRs

### Submitting Changes

1. Fork the repository
2. Create a feature branch with conventional commit format (e.g., `feat: add new exchange address`)
3. Submit a Pull Request to the `dev` branch
4. Changes will be tested in dev → staging → production environments by the Fronted Apps Team
