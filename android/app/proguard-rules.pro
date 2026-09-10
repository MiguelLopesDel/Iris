# Proguard/R8 rules for Iris Android app
-keepattributes *Annotation*, RuntimeVisibleAnnotations, AnnotationDefault

# ── kotlinx.serialization ────────────────────────────────────────────────────
# Every API response model is decoded reflectively through a generated
# serializer reachable only via the Companion. R8 cannot see those references,
# so without these the app builds fine and then fails to parse every response.

-keepclassmembers class * {
    @kotlinx.serialization.Serializable <fields>;
}

# Companion object fields of serializable classes.
-if @kotlinx.serialization.Serializable class **
-keepclassmembers class <1> {
    static <1>$Companion Companion;
}

# serializer() on companions of serializable classes.
-if @kotlinx.serialization.Serializable class ** {
    static **$* *;
}
-keepclassmembers class <2>$<3> {
    kotlinx.serialization.KSerializer serializer(...);
}

# INSTANCE.serializer() of serializable objects.
-if @kotlinx.serialization.Serializable class ** {
    public static ** INSTANCE;
}
-keepclassmembers class <1> {
    public static <1> INSTANCE;
    kotlinx.serialization.KSerializer serializer(...);
}

# ── WorkManager ──────────────────────────────────────────────────────────────
# Workers are instantiated by name from the WorkManager runtime.
-keep class * extends androidx.work.ListenableWorker {
    public <init>(android.content.Context, androidx.work.WorkerParameters);
}
