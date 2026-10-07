package dev.mw.passthrough.mixin;

import dev.mw.passthrough.MwPassthroughClient;

import net.minecraft.client.MinecraftClient;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(MinecraftClient.class)
public class ClientTickMixin {
  @Inject(method = "tick", at = @At("TAIL"))
  private void mwTick(CallbackInfo ci){
    MwPassthroughClient.onTick((MinecraftClient)(Object)this);
  }
}
