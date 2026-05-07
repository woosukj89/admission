import 'dart:async';
import 'package:google_mobile_ads/google_mobile_ads.dart';

// Replace with real AdMob ad unit IDs before release
const _rewardedAdUnitId = String.fromEnvironment(
  'ADMOB_REWARDED_AD_UNIT',
  defaultValue: 'ca-app-pub-3940256099942544/5224354917', // test ID
);

class AdService {
  static RewardedAd? _rewardedAd;
  static bool _isLoading = false;

  static Future<void> preload() async {
    if (_rewardedAd != null || _isLoading) return;
    _isLoading = true;
    await RewardedAd.load(
      adUnitId: _rewardedAdUnitId,
      request: const AdRequest(),
      rewardedAdLoadCallback: RewardedAdLoadCallback(
        onAdLoaded: (ad) { _rewardedAd = ad; _isLoading = false; },
        onAdFailedToLoad: (_) { _isLoading = false; },
      ),
    );
  }

  static Future<bool> showRewardedAd() async {
    if (_rewardedAd == null) await preload();
    final ad = _rewardedAd;
    if (ad == null) return false;

    final completer = Completer<bool>();
    ad.fullScreenContentCallback = FullScreenContentCallback(
      onAdDismissedFullScreenContent: (a) { a.dispose(); _rewardedAd = null; },
      onAdFailedToShowFullScreenContent: (a, _) {
        a.dispose(); _rewardedAd = null;
        if (!completer.isCompleted) completer.complete(false);
      },
    );
    ad.show(onUserEarnedReward: (_, reward) {
      if (!completer.isCompleted) completer.complete(true);
    });
    preload(); // preload next ad in background
    return completer.future.timeout(const Duration(minutes: 2), onTimeout: () => false);
  }
}
