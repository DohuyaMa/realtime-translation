{ lib
, python3
, src
, pname
, dependencies
, projectFile ? "pyproject.toml"
}:

python3.pkgs.buildPythonApplication {
  inherit pname src;
  version = "0.1.0";

  pyproject = true;

  nativeBuildInputs = with python3.pkgs; [ setuptools ];

  propagatedBuildInputs = dependencies;

  meta = with lib; {
    license = licenses.mit;
    maintainers = [ ];
  };
}