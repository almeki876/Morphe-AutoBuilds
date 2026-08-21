package gplaydl

import com.aurora.gplayapi.data.models.AuthData
import com.aurora.gplayapi.data.providers.DeviceInfoProvider
import com.aurora.gplayapi.helpers.AuthHelper
import java.util.Locale
import java.util.Properties

/** Keeps the GPlayApi-specific authentication boundary in one place. */
class GPlaySession(
    val authData: AuthData,
) {
    companion object {
        fun aas(
            email: String,
            aasToken: String,
            properties: Properties,
            locale: Locale,
        ): GPlaySession {
            // This JVM-compatible GPlayApi's AAS builder uses Locale.getDefault().
            Locale.setDefault(locale)
            return GPlaySession(AuthHelper.build(email, aasToken, properties))
        }

        fun authToken(
            email: String,
            authToken: String,
            properties: Properties,
            locale: Locale,
        ): GPlaySession {
            val provider = DeviceInfoProvider(properties, locale.toString())
            return GPlaySession(
                AuthHelper.buildInsecure(email, authToken, locale, provider)
            )
        }

        fun anonymous(
            dispenserAuth: DispenserAuth,
            properties: Properties,
            locale: Locale,
        ): GPlaySession = authToken(
            dispenserAuth.email,
            dispenserAuth.authToken,
            properties,
            locale,
        )
    }
}
