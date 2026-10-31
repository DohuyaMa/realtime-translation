# Vosk (Kaldi) Python bindings: not in nixpkgs. The upstream manylinux wheel
# bundles libvosk.so (Kaldi + OpenBLAS statically linked; needs only libstdc++,
# libgcc_s, libm, libc), loaded through cffi ABI mode (dlopen, no compiled
# extension). autoPatchelfHook points it at the nixpkgs C++ runtime.
{ lib, stdenv, python3Packages, fetchurl, autoPatchelfHook }:
python3Packages.buildPythonPackage rec {
  pname = "vosk";
  version = "0.3.45";
  format = "wheel";
  src = fetchurl {
    url = "https://files.pythonhosted.org/packages/py3/v/vosk/vosk-${version}-py3-none-manylinux_2_12_x86_64.manylinux2010_x86_64.whl";
    hash = "sha256-JeAlCTxDmdcnj1Q1aO2MxUYKw6S/SMI2c6zh4l0mYZ8=";
  };
  nativeBuildInputs = [ autoPatchelfHook ];
  buildInputs = [ stdenv.cc.cc.lib ];
  dependencies = with python3Packages; [ cffi requests tqdm srt websockets ];
  # The wheel's `vosk-transcriber` CLI is not used by rt-vosk.
  postInstall = ''
    rm -f $out/bin/vosk-transcriber
  '';
  pythonImportsCheck = [ "vosk" ];
  meta = {
    description = "Offline speech recognition API based on Kaldi (prebuilt libvosk)";
    homepage = "https://alphacephei.com/vosk/";
    license = lib.licenses.asl20;
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
    platforms = [ "x86_64-linux" ];
  };
}
