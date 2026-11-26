## GitChain System Block Diagram

There are five stakeholders in the GitChain system.

1. **Developer** writes code and runs `git push`, triggering the entire pipeline.
2. **GitHub** hosts the repository and fires the GitHub Actions workflow on every push.
3. **GitHub Action (CI Bot)** computes the diff hash, builds + signs the transaction payload with Ed25519, and POSTs it to the GitChain node.
4. **GitChain Node** validates the signature, stores the transaction in the mempool, mines it into a block, persists to `chain.json`, and serves the REST API + Dashboard.
5. **Peer Nodes** receive new blocks and transactions via P2P gossip, independently validate each block, and maintain their own full chain copy — ensuring no single node can rewrite history.

```mermaid
graph TB
    %% ── Stakeholders ──────────────────────────────────────────────────────────

    subgraph DEV["Developer Workstation"]
        D1["git push"]
        D2["gitchain verify &lt;sha&gt;"]
        D3["gitchain keygen"]
    end

    subgraph GH["GitHub"]
        GH1["Repository\n(code + commits)"]
        GH2["Actions Runner\n(CI environment)"]
        GH1 -->|"on: push\ntrigger"| GH2
    end

    subgraph ACTION["GitHub Action (CI Bot)"]
        A1["Read env vars\nGITHUB_SHA · GITHUB_ACTOR\nGITHUB_REPOSITORY · GITHUB_REF_NAME"]
        A2["Compute diff_summary\ngit show --stat HEAD"]
        A3["Compute diff_hash\nSHA-256(git show -p HEAD)"]
        A4["Build 8-field payload\nauthor · branch · commit_hash · diff_hash\ndiff_summary · owner_pubkey · repo · timestamp"]
        A5["payload_hash = SHA-256(canonical JSON)"]
        A6["signature = Ed25519.sign(payload_hash,\nGITCHAIN_PRIVATE_KEY)"]
        A1 --> A2 --> A3 --> A4 --> A5 --> A6
    end

    subgraph NODE["GitChain Node (Primary)"]
        N1["REST API\nFastAPI :8000"]
        N2["Payload Hash\nVerification"]
        N3["Signature\nVerification\n(Ed25519)"]
        N4["Mempool\n(pending txs)"]
        N5["Mining Loop\nPoW SHA-256\nnonce until 000..."]
        N6["Blockchain\nchain.json"]
        N7["Dashboard\n/dashboard"]
        N1 --> N2 --> N3 --> N4 --> N5 --> N6
        N6 -.->|"serves blocks"| N7
        N6 -.->|"GET /verify\nGET /chain\nGET /contributions"| N1
    end

    subgraph IHP["Peer Nodes (Immutable Hash Pool)"]
        P1["Peer Node A\nFull Chain Copy"]
        P2["Peer Node B\nFull Chain Copy"]
        P3["Peer Node C\nFull Chain Copy"]
    end

    subgraph AUDITOR["Auditor / Verifier"]
        V1["GET /verify/&lt;sha&gt;"]
        V2["SHA-256 check\ngit show -p &lt;sha&gt; | sha256sum\nvs on-chain diff_hash"]
    end

    %% ── Data flows ────────────────────────────────────────────────────────────

    D1 -->|"git push"| GH1
    D2 -->|"curl or CLI"| N1
    D3 -.->|"generates\nGITCHAIN_PRIVATE_KEY"| ACTION

    GH2 -->|"runs"| ACTION

    A6 -->|"POST /transaction\n{10 fields incl. diff_hash}"| N1

    N6 -->|"NEW_BLOCK\ngossip (TCP P2P)"| IHP
    N4 -->|"NEW_TRANSACTION\ngossip (TCP P2P)"| IHP
    IHP -->|"validate block\n(hash · PoW · index · prev_hash)\nthen append"| IHP
    IHP -->|"GET_CHAIN on connect\n→ CHAIN_RESPONSE\n(longest chain wins)"| N6

    N1 -->|"on_chain · diff_hash\nblock_index · author"| AUDITOR
    V1 --> V2

    %% ── Styling ───────────────────────────────────────────────────────────────

    classDef stakeholder fill:#dbeafe,stroke:#3b82f6,color:#1e3a5f
    classDef storage    fill:#d1fae5,stroke:#10b981,color:#064e3b
    classDef crypto     fill:#fef9c3,stroke:#ca8a04,color:#713f12
    classDef peer       fill:#ede9fe,stroke:#7c3aed,color:#2e1065

    class DEV,GH,AUDITOR stakeholder
    class N6,IHP storage
    class A5,A6,N2,N3 crypto
    class P1,P2,P3 peer
```

### Key Data Flows

| Flow | What travels | Purpose |
|---|---|---|
| Developer → GitHub | `git push` | Triggers the pipeline |
| GitHub → Action | Env vars (SHA, actor, repo) | Provides commit identity |
| Action → Node | Signed 10-field transaction | Records commit on-chain |
| Node → Peers | `NEW_BLOCK` / `NEW_TRANSACTION` (TCP) | Decentralized replication |
| Peers → Node | `CHAIN_RESPONSE` on connect | Syncs to longest valid chain |
| Auditor → Node | `GET /verify/<sha>` | Returns `diff_hash` + `author` for proof |
| Auditor → Git | `git show -p <sha>` | Reproduce diff; compare SHA-256 to `diff_hash` |
