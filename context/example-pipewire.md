Example Sink

The example sink is a good starting point for writing a custom sink. We refer to the source code for more information.

# Module Name

`libpipewire-module-example-sink`

# Module Options

* `node.name`: a unique name for the stream
* `node.description`: a human readable name for the stream
* `stream.props = {}`: properties to be passed to the stream

# General options

Options with well-known behavior.

* [PW_KEY_REMOTE_NAME](https://docs.pipewire.org/group__pw__keys.html#ga86dd4aa9894e3d6deb9570988e37f6ca)
* [PW_KEY_AUDIO_FORMAT](https://docs.pipewire.org/group__pw__keys.html#ga0b13a2631ffb67076f67105d1ca347d6)
* [PW_KEY_AUDIO_RATE](https://docs.pipewire.org/group__pw__keys.html#gaf4643e961de004488943e2ab0322ac4c)
* [PW_KEY_AUDIO_CHANNELS](https://docs.pipewire.org/group__pw__keys.html#ga7f63a5688e90b4d78e8db0c7a18bd959)
* [SPA_KEY_AUDIO_POSITION](https://docs.pipewire.org/group__spa__param.html#ga44f9cdcfe2f186531692d443487cbce7)
* [PW_KEY_MEDIA_NAME](https://docs.pipewire.org/group__pw__keys.html#gac8cc358cf31f66cde84ed87a9a2fb2e7)
* [PW_KEY_NODE_LATENCY](https://docs.pipewire.org/group__pw__keys.html#ga6d08c36bb4d3bcae49e822023a8089e2)
* [PW_KEY_NODE_NAME](https://docs.pipewire.org/group__pw__keys.html#gaaa6e55cb44dbfe8af1a9e8531abe6f09)
* [PW_KEY_NODE_DESCRIPTION](https://docs.pipewire.org/group__pw__keys.html#ga4d4e1af1a950aafe7b4184a89fa4e016)
* [PW_KEY_NODE_GROUP](https://docs.pipewire.org/group__pw__keys.html#ga5b8ea75d1a6f9a1d0d0e60e60eeae5a8)
* [PW_KEY_NODE_VIRTUAL](https://docs.pipewire.org/group__pw__keys.html#gad47c9bd7ac42e326f5879cac7b73df6f)
* [PW_KEY_MEDIA_CLASS](https://docs.pipewire.org/group__pw__keys.html#gaed255c860a65813289cb4c4620243da6)

# Example configuration

# ~/.config/pipewire/pipewire.conf.d/my-example-sink.conf

context.modules = [

{   name = libpipewire-module-example-sink

    args = {

    node.name = "example_sink"

    node.description = "My Example Sink"

    stream.props = {

    audio.position = [ FL FR ]

    }

    }

}

]



Example Source

The example source is a good starting point for writing a custom source. We refer to the source code for more information.

# Module Name

`libpipewire-module-example-source`

# Module Options

* `node.name`: a unique name for the stream
* `node.description`: a human readable name for the stream
* `stream.props = {}`: properties to be passed to the stream

# General options

Options with well-known behavior.

* [PW_KEY_REMOTE_NAME](https://docs.pipewire.org/group__pw__keys.html#ga86dd4aa9894e3d6deb9570988e37f6ca)
* [PW_KEY_AUDIO_FORMAT](https://docs.pipewire.org/group__pw__keys.html#ga0b13a2631ffb67076f67105d1ca347d6)
* [PW_KEY_AUDIO_RATE](https://docs.pipewire.org/group__pw__keys.html#gaf4643e961de004488943e2ab0322ac4c)
* [PW_KEY_AUDIO_CHANNELS](https://docs.pipewire.org/group__pw__keys.html#ga7f63a5688e90b4d78e8db0c7a18bd959)
* [SPA_KEY_AUDIO_POSITION](https://docs.pipewire.org/group__spa__param.html#ga44f9cdcfe2f186531692d443487cbce7)
* [PW_KEY_MEDIA_NAME](https://docs.pipewire.org/group__pw__keys.html#gac8cc358cf31f66cde84ed87a9a2fb2e7)
* [PW_KEY_NODE_LATENCY](https://docs.pipewire.org/group__pw__keys.html#ga6d08c36bb4d3bcae49e822023a8089e2)
* [PW_KEY_NODE_NAME](https://docs.pipewire.org/group__pw__keys.html#gaaa6e55cb44dbfe8af1a9e8531abe6f09)
* [PW_KEY_NODE_DESCRIPTION](https://docs.pipewire.org/group__pw__keys.html#ga4d4e1af1a950aafe7b4184a89fa4e016)
* [PW_KEY_NODE_GROUP](https://docs.pipewire.org/group__pw__keys.html#ga5b8ea75d1a6f9a1d0d0e60e60eeae5a8)
* [PW_KEY_NODE_VIRTUAL](https://docs.pipewire.org/group__pw__keys.html#gad47c9bd7ac42e326f5879cac7b73df6f)
* [PW_KEY_MEDIA_CLASS](https://docs.pipewire.org/group__pw__keys.html#gaed255c860a65813289cb4c4620243da6)

# Example configuration

# ~/.config/pipewire/pipewire.conf.d/my-example-source.conf

context.modules = [

{   name = libpipewire-module-example-source

    args = {

    node.name = "example_source"

    node.description = "My Example Source"

    stream.props = {

    audio.position = [ FL FR ]

    }

    }

}

]
