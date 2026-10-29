package com.example.chat_app

import com.ryanheise.audioservice.AudioServiceActivity
import io.flutter.embedding.engine.FlutterEngine

class MainActivity : AudioServiceActivity() {
    private var pcmStream: PcmStreamChannel? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        pcmStream = PcmStreamChannel(applicationContext, flutterEngine.dartExecutor.binaryMessenger)
    }
}
