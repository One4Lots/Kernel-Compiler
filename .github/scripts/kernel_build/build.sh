#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
: "${DEFCONFIG:?}" "${IMAGE_TYPE:?}" "${TOOLCHAIN_DIR:?}" "${TOOLCHAIN_BIN_REL:?}" "${BUILD_ACTOR:?}"
# Kernel build identity follows the dispatch or rerun initiator.
export KBUILD_BUILD_USER="$BUILD_ACTOR" KBUILD_BUILD_HOST=GitHub
export PATH="$TOOLCHAIN_DIR/$TOOLCHAIN_BIN_REL:/usr/bin:$PATH"
export ARCH=arm64 SUBARCH=arm64
export CC='ccache clang' CXX='ccache clang++'
export HOSTCC='ccache /usr/bin/clang' HOSTCXX='ccache /usr/bin/clang++'
export CLANG_TRIPLE=aarch64-linux-gnu- CROSS_COMPILE=aarch64-linux-gnu-
export CROSS_COMPILE_ARM32=arm-linux-gnueabi- CROSS_COMPILE_COMPAT=arm-linux-gnueabi-
export LD=ld.lld AR=llvm-ar NM=llvm-nm STRIP=llvm-strip
export OBJCOPY=llvm-objcopy OBJDUMP=llvm-objdump READELF=llvm-readelf OBJSIZE=llvm-size
# Explicit assignments override kernel Makefile defaults for every target.
make_args=(O=out ARCH="$ARCH" SUBARCH="$SUBARCH" CC="$CC" CXX="$CXX"
  KBUILD_BUILD_USER="$KBUILD_BUILD_USER" KBUILD_BUILD_HOST="$KBUILD_BUILD_HOST"
  HOSTCC="$HOSTCC" HOSTCXX="$HOSTCXX" CLANG_TRIPLE="$CLANG_TRIPLE"
  CROSS_COMPILE="$CROSS_COMPILE" CROSS_COMPILE_ARM32="$CROSS_COMPILE_ARM32"
  CROSS_COMPILE_COMPAT="$CROSS_COMPILE_COMPAT"
  LD="$LD" AR="$AR" NM="$NM" STRIP="$STRIP" OBJCOPY="$OBJCOPY"
  OBJDUMP="$OBJDUMP" READELF="$READELF" OBJSIZE="$OBJSIZE")
mkdir -p logs
started=$(date +%s)
printf '%s\n' "$started" > logs/build-started.txt
clang --version > logs/clang-version.txt
ld.lld --version > logs/lld-version.txt
ccache --zero-stats
# Defconfig symlinks must resolve inside the configuration tree.
config=$(realpath -e "kernel/arch/arm64/configs/$DEFCONFIG")
[[ "$config" == "$(realpath kernel)/arch/arm64/configs/"* ]] || die 'Defconfig resolves outside arch/arm64/configs'
log "Configuring $BRANCH with $DEFCONFIG"
make -C kernel "${make_args[@]}" "$DEFCONFIG" 2>&1 | tee logs/defconfig.log
log "Compiling $IMAGE_TYPE with $(nproc) jobs"
make -C kernel -j"$(nproc)" "${make_args[@]}" 2>&1 | tee logs/build.log
case "$IMAGE_TYPE" in
  'Image.gz.dtb'|'Image.gz + dtbo.img + dtb.img') image=Image.gz ;;
  'Image.lz4 + dtbo.img + dtb.img') image=Image.lz4 ;;
  *) die 'Unsupported image mode' ;;
esac
# Build the requested compression if the default target omitted it.
if [[ ! -s "kernel/out/arch/arm64/boot/$image" ]]; then
  make -C kernel -j"$(nproc)" "${make_args[@]}" "$image" 2>&1 | tee -a logs/build.log
fi
# Build device trees when the default target omits them.
if ! find kernel/out/arch/arm64/boot -type f -name '*.dtb' -print -quit | grep -q .; then
  make -C kernel -j"$(nproc)" "${make_args[@]}" dtbs 2>&1 | tee -a logs/build.log
fi
if [[ "$IMAGE_TYPE" != Image.gz.dtb && ! -s kernel/out/arch/arm64/boot/dtbo.img ]]; then
  make -C kernel -j"$(nproc)" "${make_args[@]}" dtbo.img 2>&1 | tee -a logs/build.log
fi
printf '%s\n' "$(( $(date +%s) - started ))" > logs/build-duration.txt
log 'Compilation finished'
ccache --show-stats | tee logs/ccache-stats.txt
