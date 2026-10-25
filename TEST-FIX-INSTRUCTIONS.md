## HOW TO TEST THE FIX

The modkit loader is cached in the page. Follow these steps **exactly**:

### 1. Clear the bridge flag
Open browser console on the BuildNow GG game page:
```js
GM_setValue('bngReplayBridgeEnabled', false)
```

### 2. Hard refresh to clear ALL caches
- Chrome/Edge: `Ctrl+Shift+R` or `Ctrl+F5`
- Firefox: `Ctrl+Shift+R`
- Or open DevTools → Network tab → check "Disable cache" → refresh

### 3. Re-arm the bridge
Click "Enable Bridge" in the replay panel

### 4. Hard refresh AGAIN
Another `Ctrl+Shift+R` so the userscript fetches the NEW loader from the proxy

### 5. Check console
Look for:
- ✅ No `CompileError: expected 0 elements on the stack for fallthru, found 4`
- ✅ `[UnityWebModkit] [MESSAGE] Chainloader startup complete`
- ✅ Game loads into lobby

---

If you still see the error after step 5, the fix didn't work and I need to try a different approach.

If the error is GONE but you see different errors (hooks not applying, etc.), the fix worked and those are separate issues.
