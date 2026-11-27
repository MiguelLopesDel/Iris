package com.iris.app.ui.text

import androidx.annotation.ArrayRes
import androidx.annotation.PluralsRes
import androidx.annotation.StringRes
import androidx.compose.runtime.Composable
import androidx.compose.ui.res.pluralStringResource
import com.iris.app.IrisApplication

/**
 * Resolves strings outside composition (view models, data layer) in the locale the app
 * currently runs with. Plain JVM unit tests have no application, so they install a
 * [Resolver] that reads the same resource files; without either, the result is empty.
 */
interface Resolver {
    fun string(id: Int, args: Array<out Any>): String
    fun plural(id: Int, count: Int, args: Array<out Any>): String
    fun stringArray(id: Int): Array<String>
}

object AppStrings {
    @Volatile
    var resolver: Resolver? = null
}

fun appString(@StringRes id: Int, vararg args: Any): String =
    AppStrings.resolver?.string(id, args)
        ?: runCatching { IrisApplication.instance.getString(id, *args) }.getOrDefault("")

/**
 * The quantity used to pick a plural form. CLDR files zero under "one" in Portuguese,
 * which reads "0 mídia"; both supported languages say "0 mídias" / "0 items", so zero
 * is routed to "other" (2 is "other" in pt and en alike).
 */
fun pluralQuantity(count: Int): Int = if (count == 0) 2 else count

/** Plural counterpart of [appString]. */
fun appPlural(@PluralsRes id: Int, count: Int, vararg args: Any): String {
    val quantity = pluralQuantity(count)
    return AppStrings.resolver?.plural(id, quantity, args)
        ?: runCatching { IrisApplication.instance.resources.getQuantityString(id, quantity, *args) }
            .getOrDefault("")
}

/** Compose counterpart of [appPlural], with the same zero handling. */
@Composable
fun countPluralResource(@PluralsRes id: Int, count: Int, vararg args: Any): String =
    pluralStringResource(id, pluralQuantity(count), *args)

/** Array counterpart of [appString]. */
fun appStringArray(@ArrayRes id: Int): Array<String> =
    AppStrings.resolver?.stringArray(id)
        ?: runCatching { IrisApplication.instance.resources.getStringArray(id) }.getOrDefault(emptyArray())
