{ lib
, python3
, buildPythonApplication
, fetchPypi
, ...
}:

buildPythonApplication {
  name = "translator-capture";
  
  src = ./../../src;
  
  pyproject = true;
  build-system = [ "setuptools" ];
  
  propagatedBuildInputs = with python3.pkgs; [
    # Capture service specific dependencies
    pyaudio
    sounddevice
    numpy
    soundfile
    librosa
    
    pulsectl
    pyyaml
    python-dotenv
    loguru
    # Add any other capture-specific dependencies
  ];
  
  buildInputs = with python3.pkgs; [
    # Build-time dependencies
  ];
  
  # Define console script for capture service
  pythonPath = with python3.pkgs; [
    pyaudio
    sounddevice
    numpy
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
    makeWrapper ${python3.interpreter} $out/bin/translator-capture \
      --add-flags "-c \"import sys; sys.path.insert(0, '$out/lib/${python3.libPrefix}/site-packages'); from capture.capture_service import main; main()\""
  };
  
  passthru = {
    exeName = "translator-capture";
  };
  
  meta = with lib; {
    description = "Audio capture service for real-time translation";
    homepage = "https://github.com/dmaslo/real-time-transletor";
    license = licenses.mit;
    maintainers = [ ];
  };
}
