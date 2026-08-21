package gplaydl

import java.nio.file.Path
import java.util.Locale

private data class CliOptions(
    val command: String,
    val packages: List<String>,
    val output: Path,
    val versionCode: Long?,
    val deviceProperties: Path?,
    val userAgent: String,
    val locale: Locale,
)

private fun parseArgs(args: Array<String>): CliOptions {
    require(args.isNotEmpty()) {
        "usage: gplaydl <auth|download> [package ...] [--output DIR] [--version-code N] [--device-properties FILE] [--aurora-user-agent UA] [--locale TAG]"
    }
    val command = args[0]
    require(command == "auth" || command == "download") { "unknown command: $command" }

    var output = Path.of(".")
    var versionCode: Long? = null
    var deviceProperties: Path? = null
    var userAgent = "com.aurora.store-4.8.4-76"
    var locale = Locale.JAPAN
    val packages = mutableListOf<String>()

    var index = 1
    while (index < args.size) {
        when (val arg = args[index]) {
            "--output" -> output = Path.of(args.getOrNull(++index) ?: error("--output requires a value"))
            "--version-code" -> versionCode = (args.getOrNull(++index) ?: error("--version-code requires a value")).toLong()
            "--device-properties" -> deviceProperties = Path.of(args.getOrNull(++index) ?: error("--device-properties requires a value"))
            "--aurora-user-agent" -> userAgent = args.getOrNull(++index) ?: error("--aurora-user-agent requires a value")
            "--locale" -> locale = Locale.forLanguageTag(args.getOrNull(++index) ?: error("--locale requires a value"))
            else -> {
                require(!arg.startsWith("--")) { "unknown option: $arg" }
                packages += arg
            }
        }
        index++
    }

    if (command == "download") require(packages.isNotEmpty()) { "download requires at least one package" }
    if (packages.size > 1 && versionCode != null) {
        error("--version-code can only be used when downloading one package")
    }
    return CliOptions(command, packages, output, versionCode, deviceProperties, userAgent, locale)
}

fun main(args: Array<String>) {
    val options = parseArgs(args)
    val properties = loadDeviceProperties(options.deviceProperties)
    val dispenserAuth = AnonymousAuthClient(userAgent = options.userAgent).login(properties)

    if (options.command == "auth") {
        println("Anonymous login OK")
        println("Account: ${dispenserAuth.email}")
        return
    }

    // Build one authenticated session and reuse it for every package requested
    // by this process. Never print or persist the dispenser authToken.
    val session = GPlaySession.anonymous(dispenserAuth, properties, options.locale)
    val downloader = PlayDownloader(session)
    for (packageName in options.packages) {
        val packageOutput = options.output.resolve(packageName)
        val result = downloader.download(
            packageName = packageName,
            outputDir = packageOutput,
            requestedVersionCode = options.versionCode,
        )
        println(
            "Downloaded ${result.packageName} versionName=${result.versionName} " +
                "versionCode=${result.versionCode} files=${result.files.size}"
        )
    }
}
