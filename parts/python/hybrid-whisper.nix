{ lib
, python3
, buildPythonApplication
, fetchPypi
, ...
}:

buildPythonApplication {
  name = "translator-hybrid-whisper";
  
  src = ./../../src;
  
  pyproject = true;
  build-system = [ "setuptools" ];
  
  propagatedBuildInputs = with python3.pkgs; [
    # Hybrid Whisper service specific dependencies
    faster-whisper
    kokoro-onnx
    numpy
    onnxruntime
    soundfile
    librosa
    
    pulsectl
    pyyaml
    python-dotenv
    loguru
    # Add any other hybrid-whisper-specific dependencies
  ];
  
  buildInputs = with python3.pkgs; [
    # Build-time dependencies
  ];
  
  # Define console script for hybrid-whisper service
  pythonPath = with python3.pkgs; [
    faster-whisper
    kokoro-onnx
    numpy
    onnxruntime
    soundfile
    librosa
    
    pulsectl
    pyyaml
    python-dotenv
    loguru
  ];
  
  nativeBuildInputs = with python3.pkgs; [
    makeWrapper
  ];
  
  postInstall = ''
    mkdir -p $out/bin
    makeWrapper ${python3.interpreter} $out/bin/translator-hybrid-whisper \
      --add-flags "-c 'import sys; sys.path.insert(0, \"$out/lib/${python3.libPrefix}/site-packages\"); from whisper.hybrid_whisper_service import main; main()'"
  };
  
  passthru = {
    exeName = "translator-hybrid-whisper";
  };
  
  meta = with lib; {
    description = "Hybrid Whisper speech recognition service for real-time translation";
    homepage = "https://github.com/dmaslo/real-time-transletor";
    license = licenses.mit;
    maintainers = [ ];
  };
}