package gplaydl

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.net.URI
import java.util.Properties

data class DispenserAuth(
    val email: String,
    val authToken: String,
)

class AnonymousAuthClient(
    dispenserUrl: String,
    private val apiKey: String? = null,
    private val client: OkHttpClient = OkHttpClient(),
    private val userAgent: String = "Morphe-AutoBuilds-gplaydl/1.0",
) {
    private val json = Json { ignoreUnknownKeys = true }
    private val dispenserUrl = normalizeEndpoint(dispenserUrl)

    private fun normalizeEndpoint(rawUrl: String): String {
        val trimmed = rawUrl.trim().trimEnd('/')
        require(trimmed.isNotBlank()) { "Google Play token dispenser URL is required" }
        return if (trimmed.endsWith("/api/auth")) trimmed else "$trimmed/api/auth"
    }

    fun login(properties: Properties): DispenserAuth {
        val payload = buildJsonObject {
            properties.stringPropertyNames().forEach { key ->
                put(key, properties.getProperty(key))
            }
        }
        val requestBuilder = Request.Builder()
            .url(dispenserUrl)
            .header("User-Agent", userAgent)
            .post(payload.toString().toRequestBody("application/json".toMediaType()))
        apiKey?.trim()?.takeIf { it.isNotBlank() }?.let {
            requestBuilder.header("X-Api-Key", it)
        }
        val request = requestBuilder.build()

        client.newCall(request).execute().use { response ->
            val responseBody = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                // Never propagate a dispenser response body into CI logs. A
                // custom endpoint could include tokens, account details, or a
                // large reverse-proxy challenge document in its error response.
                val host = runCatching { URI(dispenserUrl).host }.getOrNull()
                    ?.takeIf { it.isNotBlank() }
                    ?: "configured dispenser"
                error("Google Play token dispenser failed: HTTP ${response.code} from $host")
            }
            val objectValue = json.parseToJsonElement(responseBody).jsonObject
            val email = objectValue["email"]?.jsonPrimitive?.content.orEmpty()
            // Aurora-compatible dispensers historically used authToken while
            // some maintained implementations expose the same bearer token as
            // auth. Supporting both keeps the client compatible without
            // leaking or persisting either value.
            val authToken = objectValue["authToken"]?.jsonPrimitive?.content.orEmpty()
                .ifBlank { objectValue["auth"]?.jsonPrimitive?.content.orEmpty() }
            require(email.isNotBlank() && authToken.isNotBlank()) {
                "Google Play token dispenser returned incomplete credentials"
            }
            return DispenserAuth(email = email, authToken = authToken)
        }
    }
}
