{ lib
, python3
, buildPythonApplication
, fetchPypi
, pkgs
, ...
}:

buildPythonApplication {
  name = "translator-ui";
  
  src = ./../../src;
  
  pyproject = true;
  build-system = [ "setuptools" ];
  
  propagatedBuildInputs = with python3.pkgs; [
    # UI service specific dependencies
    pyqt5
    pyside6
    # Add any other UI-specific dependencies
  ];
  
  buildInputs = with python3.pkgs; [
    # Build-time dependencies
  ];
  
  # Define console script for UI service
  pythonPath = with python3.pkgs; [
    pyqt5
    pyside6
  ];
  
  nativeBuildInputs = with python3.pkgs; [
    makeWrapper
  ];
  
  postInstall = ''
    mkdir -p $out/bin
    makeWrapper ${python3.interpreter} $out/bin/translator-ui \
      --add-flags "-c 'import sys; sys.path.insert(0, \"$out/lib/${python3.libPrefix}/site-packages\"); from ui.qml.main import main; main()'"
  };
  
  passthru = {
    exeName = "translator-ui";
  };
  
  meta = with lib; {
    description = "UI service for real-time translation";
    homepage = "https://github.com/dmaslo/real-time-transletor";
    license = licenses.mit;
    maintainers = [ ];
  };
}