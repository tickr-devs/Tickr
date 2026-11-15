import UIKit

/// The pushed screens hide the navigation bar and draw their own "‹ Home" (`BackButton`), which
/// turns off UIKit's edge swipe back. The navigation controller is its own swipe's delegate here,
/// so the swipe works again whenever there is a screen to go back to.
extension UINavigationController: @retroactive UIGestureRecognizerDelegate {
  override open func viewDidLoad() {
    super.viewDidLoad()
    interactivePopGestureRecognizer?.delegate = self
  }

  public func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
    viewControllers.count > 1
  }
}
