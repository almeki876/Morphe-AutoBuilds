package gplaydl

import com.aurora.gplayapi.DeviceManager
import java.nio.file.Files
import java.nio.file.Path
import java.util.Properties

private val BUNDLED_DEVICE_PROFILES = listOf(
    "px_3a.properties",
    "ad_g3_pro.properties",
    "fp_2.properties",
    "hw_h9.properties",
    "hw_mate20.properties",
    "mi_8_se.properties",
    "mi_a1.properties",
    "mi_mix2.properties",
    "moto_g5.properties",
    "nk_8.properties",
)

/**
 * Return device profiles to try for anonymous dispenser authentication.
 *
 * A caller-supplied properties file is authoritative and therefore yields one
 * candidate. Otherwise rotate through real Android profiles bundled with the
 * JVM-compatible GPlayApi. Anonymous dispensers may reject a profile even
 * when another one is accepted, so pinning CI to one old Pixel profile makes
 * anonymous login unnecessarily fragile.
 */
fun loadDevicePropertyCandidates(path: Path?): List<Pair<String, Properties>> {
    if (path != null) {
        require(Files.isRegularFile(path)) { "device.properties not found: $path" }
        val properties = Properties().apply {
            Files.newInputStream(path).use { load(it) }
        }
        return listOf(path.fileName.toString() to properties)
    }

    val candidates = BUNDLED_DEVICE_PROFILES.mapNotNull { name ->
        DeviceManager.loadProperties(name)?.let { name to it }
    }
    require(candidates.isNotEmpty()) {
        "GPlayApi did not expose any bundled Android device profiles"
    }
    return candidates
}

fun loadDeviceProperties(path: Path?): Properties =
    loadDevicePropertyCandidates(path).first().second
