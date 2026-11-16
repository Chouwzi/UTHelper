Pod::Spec.new do |s|
  s.name             = 'flet_uth_background_sync'
  s.version          = '0.1.0'
  s.summary          = 'Native deadline notification scheduling for UTHelper.'
  s.description      = <<-DESC
Schedules UTHelper deadline reminders with iOS UserNotifications.
                       DESC
  s.homepage         = 'https://github.com/Chouwzi/UTHelper'
  s.license          = { :type => 'PolyForm Noncommercial 1.0.0' }
  s.author           = { 'UTHelper' => 'noreply@uthelper.local' }
  s.source           = { :path => '.' }
  s.source_files = 'Classes/**/*.swift'
  s.dependency 'Flutter'
  s.platform = :ios, '13.0'
  s.swift_version = '5.0'
end
