package gplaydl

import com.aurora.gplayapi.DeviceManager
import java.nio.file.Files
import java.nio.file.Path
import java.util.Properties

fun loadDeviceProperties(path: Path?): Properties {
    if (path != null) {
        require(Files.isRegularFile(path)) { "device.properties not found: $path" }
        return Properties().apply {
            Files.newInputStream(path).use { load(it) }
        }
    }

    // Use a real Android device profile bundled with the JVM-compatible
    // GPlayApi rather than synthesizing values from the CI host.
    return DeviceManager.loadProperties("px_3a.properties")
        ?: error("GPlayApi bundled device profile px_3a.properties was not found")
}
