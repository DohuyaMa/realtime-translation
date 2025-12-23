#!/usr/bin/env python3
"""Create PipeWire configuration for virtual sinks."""

from pathlib import Path

def create_pipewire_config():
    config_dir = Path.home() / ".config" / "pipewire" / "pipewire.conf.d"
    config_dir.mkdir(parents=True, exist_ok=True)

    config_content = '''context.modules = [
  {
    name = libpipewire-module-null-sink
    args = {
      node.name = "rt_virtual_input"
      node.description = "RT-Virtual-Input"
      media.class = "Audio/Sink"
      stream.props = { audio.position = [ FL FR ]; }
    }
  }
  {
    name = libpipewire-module-null-sink
    args = {
      node.name = "rt_virtual_output"
      node.description = "RT-Virtual-Output"
      media.class = "Audio/Sink"
      stream.props = { audio.position = [ FL FR ]; }
    }
  }
]
'''

    config_file = config_dir / "30-rt-virtual-sinks.conf"
    config_file.write_text(config_content)

    print(f"Created PipeWire configuration at {config_file}")
    print("Restart PipeWire to apply changes:")
    print("  systemctl --user restart pipewire pipewire-pulse")

if __name__ == "__main__":
    create_pipewire_config()
