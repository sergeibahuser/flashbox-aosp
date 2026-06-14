#!/bin/bash
set -euo pipefail
echo "FlashBox build start"
MANIFEST_URL="${INPUT_MANIFEST_URL:-}"
BRANCH="${INPUT_BRANCH:-main}"
DEVICE="${INPUT_DEVICE:-a52}"
ROM="${INPUT_ROM:-LineageOS}"
PRESERVE_VENDOR="${INPUT_PRESERVE_VENDOR:-true}"
echo "Manifest: $MANIFEST_URL"
echo "Branch: $BRANCH"
echo "Device: $DEVICE"
echo "ROM: $ROM"
echo "Preserve vendor: $PRESERVE_VENDOR"
if ! command -v repo >/dev/null 2>&1; then
  echo "Installing repo..."
  curl -sS https://storage.googleapis.com/git-repo-downloads/repo > /usr/local/bin/repo
  chmod a+x /usr/local/bin/repo
fi
WORKDIR="$PWD/work"
mkdir -p "$WORKDIR"
cd "$WORKDIR"
if [ -n "$MANIFEST_URL" ]; then
  repo init -u "$MANIFEST_URL" -b "$BRANCH" || true
else
  echo "No manifest provided; abort"
  exit 1
fi
repo sync -j8 --no-clone-bundle || true
if [ "$PRESERVE_VENDOR" = "true" ]; then
  echo "Attempting to fetch vendor blobs..."
  if [ -n "${{ secrets.VENDOR_BLOBS_URL }}" ]; then
    mkdir -p vendor_blobs
    curl -sSL "${{ secrets.VENDOR_BLOBS_URL }}" -o vendor_blobs/vendor_blobs.zip || true
    if [ -f vendor_blobs/vendor_blobs.zip ]; then
      unzip -q vendor_blobs/vendor_blobs.zip -d vendor_blobs || true
      if [ -d vendor ]; then
        cp -r vendor_blobs/* vendor/ || true
      else
        mkdir -p vendor && cp -r vendor_blobs/* vendor/ || true
      fi
    fi
  else
    echo "No VENDOR_BLOBS_URL secret provided; skipping vendor blobs download"
  fi
fi
if [ -f build/envsetup.sh ]; then
  source build/envsetup.sh || true
fi
lunch aosp_${DEVICE}-userdebug || true
m -j$(nproc) || true
OUTDIR="$WORKDIR/out"
mkdir -p "$OUTDIR"
if [ -d out/target/product/$DEVICE ]; then
  cp -r out/target/product/$DEVICE "$OUTDIR/" || true
fi
ZIPNAME="flashbox_${DEVICE}_${ROM}_$(date +%Y%m%d_%H%M%S).zip"
cd "$OUTDIR"
zip -r "../$ZIPNAME" . || true
cd ..
sha256sum "$ZIPNAME" > "${ZIPNAME}.sha256"
if [ -n "${{ secrets.SIGNING_KEY }}" ]; then
  echo "Signing key present; attempt sign"
  mkdir -p /tmp/signing
  echo "${{ secrets.SIGNING_KEY }}" | base64 -d > /tmp/signing/sign_key.pem || true
  if [ -f /tmp/signing/sign_key.pem ]; then
    openssl dgst -sha256 -sign /tmp/signing/sign_key.pem -out "${ZIPNAME}.sig" "$ZIPNAME" || true
  fi
fi
mv "$ZIPNAME" "$WORKDIR/" || true
mv "${ZIPNAME}.sha256" "$WORKDIR/" || true
if [ -f "${ZIPNAME}.sig" ]; then mv "${ZIPNAME}.sig" "$WORKDIR/" || true; fi
echo "Build finished. Artifacts: $WORKDIR/$ZIPNAME"
ls -la "$WORKDIR"
