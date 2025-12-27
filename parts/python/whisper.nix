{ lib
, python3
, buildPythonApplication
, fetchPypi
, ...
}:

buildPythonApplication {
  name = "translator-whisper";
  
  src = ./../../src;
  
  pyproject = true;
  build-system = [ "setuptools" ];
  
  propagatedBuildInputs = with python3.pkgs; [
    # Whisper service specific dependencies
    whisper
    torch
    transformers
    numpy
    onnxruntime
    soundfile
    librosa
    
    pulsectl
    pyyaml
    python-dotenv
    loguru
    # Add any other whisper-specific dependencies
  ];
  
  buildInputs = with python3.pkgs; [
    # Build-time dependencies
  ];
  
  # Define console script for whisper service
  pythonPath = with python3.pkgs; [
    whisper
    torch
    transformers
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
    makeWrapper ${python3.interpreter} $out/bin/translator-whisper \
      --add-flags "-c \"import sys; sys.path.insert(0, '$out/lib/${python3.libPrefix}/site-packages'); from whisper.whisper_service import main; main()\""
  };
  passthru = {
    exeName = "translator-whisper";
  };
  
  meta = with lib; {
    description = "Whisper speech recognition service for real-time translation";
    homepage = "https://github.com/dmaslo/real-time-transletor";
    license = licenses.mit;
    maintainers = [ ];
  };
};