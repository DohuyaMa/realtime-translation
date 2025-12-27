{ lib
, python3
, buildPythonApplication
, fetchPypi
, ...
}:

{
  # Common dependencies across all services
  commonDeps = python3.pkgs.buildEnv {
    name = "translator-common-env";
    paths = with python3.pkgs; [
      # Core dependencies that all services might need
      pip
      setuptools
      wheel
    ];
  };

  # Common build inputs that can be shared
  commonBuildInputs = with python3.pkgs; [
    # Add any common build inputs here
  ];

  # Common propagated build inputs for runtime dependencies
  commonPropagatedBuildInputs = with python3.pkgs; [
    # Add common runtime dependencies here
  ];
}