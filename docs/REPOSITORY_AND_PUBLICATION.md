# Repository and publication ownership

| Role | Canonical location |
|---|---|
| Source code and future commits | https://github.com/SiYuanJasperMa/PixelWorld |
| Public videos, sanitized traces and results | https://huggingface.co/datasets/HuggingFriends/PixelWorld |
| Public dashboard repository | https://huggingface.co/spaces/HuggingFriends/PixelWorld-dashboard |
| Live monitoring | https://huggingfriends-pixelworld-dashboard.static.hf.space/index.html |

The source repository moved to `SiYuanJasperMa/PixelWorld`. The migration
preserved all three remote branches, the release tag and the original Git
history. Current documentation and publication scripts link to the new source.
The old account rejected a native repository transfer, so GitHub stars were
not transferable. Historical commits and archived documents retain their
original provenance; they are not current publication destinations.

## Local checkouts

The shared working directory and runtime paths do not need to move when the
GitHub owner changes. Existing experiments continue using their original
absolute paths and recorded commits. Their private data is not copied into Git.

New checkouts use:

```bash
git clone https://github.com/SiYuanJasperMa/PixelWorld.git
```

For an existing checkout, confirm the destination before pushing:

```bash
git remote set-url origin https://github.com/SiYuanJasperMa/PixelWorld.git
git config remote.pushDefault origin
git config push.default simple
git remote -v
git push origin main
```

The owner's checkout keeps `legacy-origin` as a historical fetch reference,
with pushing disabled. Authentication is configured privately outside Git;
no account token is part of the repository.

## HF and dashboard updates

The HF dataset and Space remain under `HuggingFriends`; their URLs did not
change. Their README sources are tracked in
[HF_DATASET_README.md](HF_DATASET_README.md) and
[HF_SPACE_README.md](HF_SPACE_README.md). Use
`tools.publish_hf_readmes` to publish and verify those cards.

The [standard publication pipeline](HF_PUBLISHING.md) exports sanitized case
records, uploads videos and complete sanitized traces, publishes the dataset
snapshot, verifies exact bytes, then updates the Space. The main publisher
checks every five minutes; open tabs poll the dataset every 60 seconds.
Private original API bodies remain in the verified shared-storage archive.
