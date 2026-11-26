# GitChain

**A decentralized, permissionless blockchain that permanently records Git contributions.**

Every `git push` triggers a GitHub Action that signs the commit with the developer's Ed25519 key and submits it to a GitChain node. Nodes mine the commits into blocks with proof-of-work and share them over a peer-to-peer network, so a record of who committed what (and when) survives even if history is later rewritten with a force-push.

## Features

- **Signed contributions**: each commit is signed with Ed25519 (PyNaCl / tweetnacl) and validated by signature, not by a central allow-list
- **Proof-of-work blockchain**: blocks, mempool and a mining loop with configurable difficulty
- **P2P network**: TCP JSON messaging, chain sync and peer discovery over mDNS (zeroconf)
- **REST API**: FastAPI endpoints to query the chain, verify commits and submit transactions
- **Dashboard**: live web dashboard that also flags discrepancies (commits on-chain but missing from GitHub)
- **GitHub Action**: logs every push to GitChain automatically
- **CLI**: `gitchain keygen | verify | status | node start`

## Project structure

```
gitchain/
  core/            Block, Blockchain, Transaction, Mempool, Miner
  network/         P2P node, messages, chain sync, peer discovery
  api/             FastAPI server and routes
  identity/        Ed25519 key generation and signing
  cli/             gitchain command-line tool
  dashboard/       Web dashboard (HTML + JS)
  github-action/   Node.js action that submits commits
flowchart/         Mermaid architecture diagrams
tests/             pytest suite
```

## Getting started

Requires Python 3.11+.

```bash
pip install -e ".[dev]"

# Generate a keypair for a developer
gitchain keygen --name "Alice" --email alice@example.com

# Start a node (P2P on port 6331, REST API on port 8000)
gitchain node start

# Check whether a commit is on-chain
gitchain verify <commit_sha>
```

Then open the dashboard at `http://127.0.0.1:8000/dashboard`.

## Logging commits from GitHub

1. Run `gitchain keygen` and commit the updated `gitchain-registry.json`.
2. In your repository's **Settings → Secrets and variables → Actions**, add:
   - `GITCHAIN_PRIVATE_KEY`: the contents of your `.private.key` file
   - `GITCHAIN_NODE_URL`: the public URL of your GitChain node
3. The workflow in `.github/workflows/gitchain.yml` will log every push.

> Private keys (`*.private.key`) are git-ignored. Never commit them.

## API

| Method | Endpoint | Description |
|---|---|---|
| GET | `/status` | Node status |
| GET | `/chain` | Full blockchain |
| GET | `/block/{index}` | A single block |
| GET | `/contributions/{owner_pubkey}` | Contributions by a developer |
| GET | `/contributions/repo/{org}/{repo}` | Contributions to a repository |
| GET | `/verify/{commit_hash}` | Is this commit on-chain? |
| GET | `/mempool` | Pending transactions |
| POST | `/transaction` | Submit a signed commit transaction |

## Running tests

```bash
pytest
```

## Docs

- `GitChain_Architecture.pdf`: architecture overview
- `GitChain_Presentation.pdf`: project presentation
- `flowchart/`: Mermaid diagrams of each flow
